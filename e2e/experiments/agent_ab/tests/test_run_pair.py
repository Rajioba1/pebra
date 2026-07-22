"""Pin the fail-closed gate INSIDE the agent-invocation path. This guards the safety check across
refactors: if the check_gate() call at the top of _invoke_subject_agent were removed, these fail. Now
that Phase G has removed the AnthropicClient.send NotImplementedError stop, the gate is the SOLE guard,
so this pin is the last line of defence against an accidental live run."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import replace

from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import SubjectResult, TaskSpec, ToolCallRecord
from e2e.experiments.agent_ab.runners import (
    agent_loop, arm_prep, evaluator, model_client, run_control, run_gate, run_pair,
    subject_protocol,
)
from e2e.experiments.agent_ab.tools import advisory_contract

_SPEC = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)


def _dummy_setup(tmp_path):
    return run_pair.ArmSetup(
        arm="control", repo_path=tmp_path, advisory_backend=lambda payload: {},
        baseline_build=None, subject_prompt="do the task",
    )


class _External:
    source_path = None
    head_sha = "abc123"


class _FakeBackend:
    def __init__(self, result=None, recorder=None):
        self.result = result or SimpleNamespace(available=True, ran=True, passed=True, error_summary="")
        self.recorder = recorder

    def run_build_delta(self, repo_path, spec, *, baseline_keys=None):
        if self.recorder is not None:
            self.recorder(repo_path, spec, baseline_keys)
        return self.result

    def run_build(self, repo_path, spec):
        return self.result

    def run_tests(self, repo_path, spec, *, project=None, test_filter=None):
        return self.result


def _close_gate(mp):
    mp.delenv("E2E_AB_RUN", raising=False)
    mp.delenv("E2E_EXTERNAL", raising=False)
    mp.delenv("ANTHROPIC_API_KEY", raising=False)


def test_invoke_subject_agent_gated_fail_closed(monkeypatch, tmp_path):
    _close_gate(monkeypatch)
    # The gate is the FIRST statement; it must raise before any config load / AnthropicClient / clone.
    with pytest.raises(run_gate.RunGateError):
        run_pair._invoke_subject_agent(_dummy_setup(tmp_path), _SPEC, 0)


def test_incompatible_gate_contract_aborts_before_provider_setup(monkeypatch, tmp_path):
    setup = _dummy_setup(tmp_path)
    setup.arm = models.ARM_TREATMENT
    setup.gate_check_backend = lambda _event: (_ for _ in ()).throw(
        run_pair.cli_harness.GateContractError("unsupported gate contract schema")
    )
    monkeypatch.setattr(run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(
        run_pair,
        "_load_config",
        lambda: pytest.fail("provider configuration must not begin"),
    )

    with pytest.raises(run_pair.cli_harness.GateContractError, match="gate contract"):
        run_pair._invoke_subject_agent(setup, setup.spec or _SPEC, seed=1)


def test_invalid_gate_json_aborts_before_provider_setup(monkeypatch, tmp_path):
    setup = _dummy_setup(tmp_path)
    setup.arm = models.ARM_TREATMENT
    setup.gate_check_backend = run_pair._gate_check_backend(
        models.ARM_TREATMENT, tmp_path / "pebra.db"
    )
    monkeypatch.setattr(
        run_pair.cli_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="not-json", stderr=""
        ),
    )
    monkeypatch.setattr(run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(
        run_pair,
        "_load_config",
        lambda: pytest.fail("provider configuration must not begin"),
    )

    with pytest.raises(run_pair.cli_harness.GateContractError, match="gate contract"):
        run_pair._invoke_subject_agent(setup, setup.spec or _SPEC, seed=1)


def test_gate_infrastructure_outage_fails_open_to_provider_setup(monkeypatch, tmp_path):
    class ProviderSetupReached(Exception):
        pass

    setup = _dummy_setup(tmp_path)
    setup.arm = models.ARM_TREATMENT
    setup.gate_check_backend = lambda _event: (_ for _ in ()).throw(
        run_pair.cli_harness.CLIError("gate unavailable")
    )
    monkeypatch.setattr(run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(
        run_pair,
        "_load_config",
        lambda: (_ for _ in ()).throw(ProviderSetupReached),
    )

    with pytest.raises(ProviderSetupReached):
        run_pair._invoke_subject_agent(setup, setup.spec or _SPEC, seed=1)


def test_invoke_subject_agent_honors_model_env_override(monkeypatch, tmp_path):
    created: dict[str, str] = {}

    class CapturingClient:
        def __init__(self, *, model, api_key):
            created["model"] = model
            created["api_key"] = api_key

    monkeypatch.setenv("E2E_AB_RUN", "1")
    monkeypatch.setenv("E2E_EXTERNAL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("E2E_AB_MODEL", "override-model")
    monkeypatch.setattr(run_pair, "_load_config", lambda: {
        "subject": {
            "model": "config-model",
            "max_tool_calls_per_run": 5,
            "max_wall_seconds_per_run": 10,
            "max_output_tokens_per_turn": 100,
            "tools": ["read_file"],
        }
    })
    monkeypatch.setattr(model_client, "AnthropicClient", CapturingClient)
    monkeypatch.setattr(agent_loop, "run", lambda setup, spec, seed, *, client, config,
                        trace_path=None, deadline_monotonic=None: SubjectResult(
        task_id=spec.task_id, arm=setup.arm, seed=seed,
    ))
    monkeypatch.setattr(evaluator, "run_evaluator", lambda repo_path, task_id: (
        SimpleNamespace(ran=True, passed=True, error_summary=""),
        SimpleNamespace(ran=True, passed=True, error_summary=""),
        False,
    ))

    run_pair._invoke_subject_agent(_dummy_setup(tmp_path), _SPEC, 0)

    assert created == {"model": "override-model", "api_key": "sk-test"}


def test_invoke_subject_agent_can_use_deepseek_provider(monkeypatch, tmp_path):
    created: dict[str, str | None] = {}

    class CapturingClient:
        def __init__(self, *, model, api_key, base_url=None, thinking_enabled=None):
            created["model"] = model
            created["api_key"] = api_key
            created["base_url"] = base_url
            created["thinking_enabled"] = thinking_enabled

    monkeypatch.setenv("E2E_AB_RUN", "1")
    monkeypatch.setenv("E2E_EXTERNAL", "1")
    monkeypatch.setenv("E2E_AB_PROVIDER", "deepseek")
    monkeypatch.setenv("E2E_AB_THINKING", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("E2E_AB_MODEL", raising=False)
    monkeypatch.setattr(run_pair, "_load_config", lambda: {
        "subject": {
            "model": "claude-haiku-4-5-20251001",
            "max_tool_calls_per_run": 5,
            "max_wall_seconds_per_run": 10,
            "max_output_tokens_per_turn": 100,
            "tools": ["read_file"],
        }
    })
    monkeypatch.setattr(model_client, "AnthropicClient", CapturingClient)
    monkeypatch.setattr(agent_loop, "run", lambda setup, spec, seed, *, client, config,
                        trace_path=None, deadline_monotonic=None: SubjectResult(
        task_id=spec.task_id, arm=setup.arm, seed=seed,
    ))
    monkeypatch.setattr(evaluator, "run_evaluator", lambda repo_path, task_id: (
        SimpleNamespace(ran=True, passed=True, error_summary=""),
        SimpleNamespace(ran=True, passed=True, error_summary=""),
        False,
    ))

    run_pair._invoke_subject_agent(_dummy_setup(tmp_path), _SPEC, 0)

    assert created == {
        "model": "deepseek-v4-flash",
        "api_key": "deepseek-key",
        "base_url": "https://api.deepseek.com/anthropic",
        "thinking_enabled": False,
    }


def test_run_control_is_gated(monkeypatch, tmp_path):
    _close_gate(monkeypatch)
    # Bypass the real external clone; the gate inside _invoke_subject_agent must still fire.
    monkeypatch.setattr(run_control.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(run_control.run_pair, "prepare_arm",
                        lambda *a, **k: _dummy_setup(tmp_path))
    with pytest.raises(run_gate.RunGateError):
        run_control.run_control(_SPEC, 0, "rid")


def test_prepare_arm_replaces_stale_clone_and_indexes_actual_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    calls: list = []

    def _clone(_external, dest):
        assert not dest.exists()
        dest.mkdir(parents=True)
        (dest / "repo.txt").write_text("fresh")
        return dest

    def _setup_graph(*, repo_root):
        calls.append(repo_root)

    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", _setup_graph)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 700})
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend())
    stale = tmp_path / "rid" / f"T1_seed0_{run_pair._arm_token('treatment', 'rid')}" / "repo"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("stale")
    stale_db = stale.parent / "pebra.db"
    stale_db.write_bytes(b"authenticated-run-must-not-reuse-this-learning-state")

    setup = run_pair.prepare_arm(_External(), _SPEC, "treatment", 0, "rid")

    assert setup.repo_path == stale
    assert not (stale / "old.txt").exists()
    assert not stale_db.exists()
    assert (stale / "repo.txt").read_text() == "fresh"
    assert calls == [stale]


def test_treatment_gate_check_backend_uses_consult_only(monkeypatch, tmp_path):
    captured = {}

    def _gate_check(event, *, db, consult_only=False):
        captured["event"] = event
        captured["db"] = db
        captured["consult_only"] = consult_only
        return {
            "schema_version": 2,
            "permission": "allow",
            "tier": "consulted",
            "reason": None,
            "warn": None,
            "risk_summary": None,
            "matched_assessment_id": None,
        }

    monkeypatch.setattr(run_pair.cli_harness, "gate_check", _gate_check)

    db = tmp_path / "pebra.db"
    backend = run_pair._gate_check_backend("treatment", db)
    result = backend({"tool_name": "Write"})

    assert result == {
        "schema_version": 2,
        "permission": "allow",
        "tier": "consulted",
        "reason": None,
        "warn": None,
        "risk_summary": None,
    }
    assert captured == {
        "event": {"tool_name": "Write"},
        "db": db,
        "consult_only": True,
    }


def test_enforced_control_uses_unversioned_experiment_only_tier(tmp_path):
    decision = run_pair._gate_check_backend(
        models.ARM_ENFORCED_CONTROL, tmp_path / "pebra.db",
    )({"tool_name": "Write", "tool_input": {"file_path": "a.py"}})

    assert decision["tier"] == run_pair._EXPERIMENT_ONLY_POSITIVE_CONTROL_TIER
    assert "schema_version" not in decision


def test_exact_allowed_candidate_is_bound_for_post_edit_verify(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry()

    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *args, **kwargs: run_pair.advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": "proceed",
                "risk_level": "low",
                "advisory": "ok",
                "detail": {},
            },
            assessment_id="asm_7",
        ),
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "schema_version": 2,
            "permission": "allow", "tier": "consulted", "reason": None, "warn": None,
            "risk_summary": {
                "decision": "proceed", "expected_loss": 0.08, "benefit": 0.65, "rau": 0.22,
            },
            "matched_assessment_id": "asm_7",
        },
    )
    advisory = run_pair._advisory_backend(
        models.ARM_PEBRA, tmp_path, tmp_path / "pebra.db", telemetry=telemetry,
    )
    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA, tmp_path / "pebra.db", telemetry=telemetry,
    )

    advisory({"target_file": "a.cs", "proposed_patch": "diff", "change_summary": "x"})
    decision = gate({"tool_name": "Write", "tool_input": {"file_path": "a.cs"}})
    assert telemetry.applied_assessment_id is None
    run_pair._write_applied_backend(telemetry)(decision)

    assert telemetry.last_assessment_id == "asm_7"
    assert telemetry.applied_assessment_id == "asm_7"


def test_exact_allowed_candidate_binds_host_only_graph_refinement_telemetry(
    monkeypatch, tmp_path
):
    telemetry = run_pair.ArmTelemetry()
    raw_payload = {
        "recommended_decision": "proceed",
        "scores": {
            "expected_loss": 0.08,
            "benefit": 0.65,
            "expected_utility": 0.4225,
            "utility_sd": 0.158203125,
            "rau": 0.22,
            "risk_probability_updates": [{
                "fact_kind": "exported_binding_continuity",
                "provider": "materialized_codegraph",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner-a"],
                "original_probability": 0.45,
                "revised_probability": 0.1575,
                "probability_floor": 0.05,
            }],
        },
        "gates_fired": [
            {"name": "candidate_verification_passed"},
            {
                "name": "revision_risk_benefit_improved",
                "origin_expected_loss": 0.36,
                "revised_expected_loss": 0.08,
                "origin_benefit": 0.55,
                "revised_benefit": 0.65,
                "origin_expected_utility": 0.0575,
                "revised_expected_utility": 0.4225,
                "origin_utility_sd": 0.091796875,
                "revised_utility_sd": 0.158203125,
                "origin_rau": -0.06,
                "revised_rau": 0.22,
            },
        ],
        "graph_refinement": {
            "status": "available",
            "selected": True,
            "evidence": {
                "language": "typescript",
                "witness": "ecmascript",
                "witness_version": "1",
                "engine_version": "1.1.1",
                "facts": [{
                    "fact_kind": "exported_binding_continuity",
                    "event": "public_api_break",
                    "risk_source": "graph_modify_risk",
                    "owner_node_ids": ["owner-a"],
                }],
            },
        },
        "model_guidance_packet": {
            "binding": {
                "required_checks_before_commit": [
                    "run targeted tests for the touched scope before commit"
                ]
            }
        },
    }
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *args, **kwargs: run_pair.advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": "proceed",
                "risk_level": "low",
                "advisory": "ok",
                "detail": {},
            },
            assessment_id="asm_8",
            raw_payload=raw_payload,
        ),
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "schema_version": 2,
            "permission": "allow", "tier": "consulted", "reason": None, "warn": None,
            "risk_summary": {
                "decision": "proceed", "expected_loss": 0.08, "benefit": 0.65, "rau": 0.22,
            },
            "matched_assessment_id": "asm_8",
        },
    )
    advisory = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )
    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA_GRAPH_REPAIR,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )

    advisory({"target_file": "a.ts", "proposed_patch": "diff", "change_summary": "x"})
    assert telemetry.assessment_calibration_by_id["asm_8"]["expected_loss"] == 0.08
    assert telemetry.assessment_calibration_by_id["asm_8"]["benefit"] == 0.65
    decision = gate({"tool_name": "Write", "tool_input": {"file_path": "a.ts"}})
    assert telemetry.applied_graph_refinement is None
    run_pair._write_applied_backend(telemetry)(decision)

    assert telemetry.applied_graph_refinement == {
        "assessment_id": "asm_8",
        "status": "available",
        "selected": True,
        "language": "typescript",
        "witness": "ecmascript",
        "witness_version": "1",
        "engine_version": "1.1.1",
        "fact_kinds": ("exported_binding_continuity",),
        "risk_probability_update_count": 1,
        "risk_probability_updates": ({
            "event": "public_api_break",
            "risk_source": "graph_modify_risk",
            "fact_kind": "exported_binding_continuity",
            "fact_confidence": None,
            "original_probability": 0.45,
            "revised_probability": 0.1575,
            "probability_multiplier": None,
            "probability_floor": 0.05,
            "structural_probability_floor": None,
            "independent_probability_floor": None,
            "binding_term": None,
            "owner_node_ids": ("owner-a",),
            "calibration": None,
        },),
        "origin_expected_loss": 0.36,
        "revised_expected_loss": 0.08,
        "origin_benefit": 0.55,
        "revised_benefit": 0.65,
        "origin_expected_utility": 0.0575,
        "revised_expected_utility": 0.4225,
        "origin_utility_sd": 0.091796875,
        "revised_utility_sd": 0.158203125,
        "origin_rau": -0.06,
        "revised_rau": 0.22,
        "candidate_verification_passed": True,
        "revision_risk_benefit_improved": True,
        "proof_path": "graph_plus_host_verification",
        "required_checks_before_commit": (
            "run targeted tests for the touched scope before commit",
        ),
    }


def test_calibration_summary_captures_generic_scores_without_graph_refinement():
    result = SimpleNamespace(raw_payload={
        "recommended_decision": "ask_human",
        "scores": {
            "expected_loss": 0.36,
            "benefit": 0.55,
            "expected_utility": 0.0575,
            "utility_sd": 0.091796875,
            "rau": -0.06,
            "effective_threshold": 0.20,
            "calibration_lanes": {
                "benefit": {"source_type": "measured"},
                "context": {"language": "typescript", "language_tier": "full"},
            },
        },
    })

    summary = run_pair._assessment_calibration_summary(result, "asm_9")

    assert summary == {
        "assessment_id": "asm_9",
        "decision": "ask_human",
        "expected_loss": 0.36,
        "benefit": 0.55,
        "expected_utility": 0.0575,
        "utility_sd": 0.091796875,
        "rau": -0.06,
        "effective_threshold": 0.20,
        "benefit_source_type": "measured",
        "assessment_proof_class": "assessment_only",
        "language": "typescript",
        "language_tier": "full",
        "calibration_lanes": {
            "benefit": {"source_type": "measured"},
            "context": {"language": "typescript", "language_tier": "full"},
        },
    }


def test_shipped_prior_profile_omits_explicit_priors_but_keeps_task_benefit(monkeypatch):
    spec = TaskSpec(
        "JS4", "Rename helper", ("a.ts",), "risky", ("a.ts",), "test_failure", False,
        assay_p_success=0.85,
        assay_immediate_benefit=0.65,
        assay_review_cost=0.05,
    )
    monkeypatch.setenv("E2E_AB_PRIOR_MODE", "shipped")

    profile = run_pair._assay_benefit_profile(spec)

    assert profile == {"immediate_benefit": 0.65, "task": "Rename helper"}


def test_explicit_prior_profile_preserves_existing_assay_behavior(monkeypatch):
    spec = TaskSpec(
        "JS4", "Rename helper", ("a.ts",), "risky", ("a.ts",), "test_failure", False,
        assay_p_success=0.85,
        assay_immediate_benefit=0.65,
        assay_review_cost=0.05,
    )
    monkeypatch.delenv("E2E_AB_PRIOR_MODE", raising=False)

    profile = run_pair._assay_benefit_profile(spec)

    assert profile == {
        "p_success": 0.85,
        "immediate_benefit": 0.65,
        "review_cost": 0.05,
        "task": "Rename helper",
    }


def test_unknown_prior_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("E2E_AB_PRIOR_MODE", "magic")

    with pytest.raises(run_pair.RunPairError, match="E2E_AB_PRIOR_MODE"):
        run_pair._assay_benefit_profile(None)


def test_calibration_summary_captures_host_only_prior_provenance():
    result = SimpleNamespace(raw_payload={
        "recommended_decision": "proceed",
        "scores": {
            "expected_loss": 0.12,
            "benefit": 0.5,
            "expected_utility": 0.38,
            "utility_sd": 0.08,
            "rau": 0.30,
            "effective_threshold": 0.20,
            "calibration_lanes": {"context": {"language": "typescript", "language_tier": "full"}},
        },
        "prior_provenance": {
            "source": "shipped",
            "calibration_tags": ["zod_single_repo_provisional_v1"],
        },
    })

    summary = run_pair._assessment_calibration_summary(result, "asm_9")

    assert summary["prior_source"] == "shipped"
    assert summary["prior_calibration_tags"] == ["zod_single_repo_provisional_v1"]


def test_calibration_fields_bind_applied_or_restrictive_terminal_assessment():
    telemetry = run_pair.ArmTelemetry(
        last_assessment_id="asm_restrict",
        assessment_calibration_by_id={
            "asm_applied": {
                "assessment_id": "asm_applied", "decision": "proceed",
                "expected_loss": 0.08, "benefit": 0.65,
                "expected_utility": 0.42, "utility_sd": 0.15, "rau": 0.22,
                "effective_threshold": 0.20,
                "prior_source": "shipped",
                "prior_calibration_tags": ["zod_single_repo_provisional_v1"],
            },
            "asm_restrict": {
                "assessment_id": "asm_restrict", "decision": "ask_human",
                "expected_loss": 0.36, "benefit": 0.55,
                "expected_utility": 0.06, "utility_sd": 0.09, "rau": -0.06,
                "effective_threshold": 0.20,
            },
        },
    )

    restricted = run_pair._calibration_result_fields(telemetry)
    assert restricted["calibration_assessment_id"] == "asm_restrict"
    assert restricted["calibration_score_source"] == "terminal_assessment"
    assert restricted["calibration_join_valid"] is True
    assert restricted["calibration_label_scope"] == "intervention_observed"

    telemetry.applied_assessment_id = "asm_applied"
    applied = run_pair._calibration_result_fields(telemetry)
    assert applied["calibration_assessment_id"] == "asm_applied"
    assert applied["calibration_score_source"] == "applied_assessment"
    assert applied["calibration_join_valid"] is True
    assert applied["calibration_label_scope"] == "candidate_observed"
    assert applied["predicted_expected_loss"] == 0.08
    assert applied["prior_source"] == "shipped"
    assert applied["prior_calibration_tags"] == ("zod_single_repo_provisional_v1",)

    telemetry.candidate_lineage_invalidated = True
    invalidated = run_pair._calibration_result_fields(telemetry)
    assert invalidated["calibration_join_valid"] is False
    assert invalidated["calibration_label_scope"] == "unresolved"


def test_graph_refinement_summary_rejects_incoherent_fact_and_probability_update():
    result = SimpleNamespace(raw_payload={
        "scores": {
            "expected_loss": 0.08,
            "rau": 0.22,
            "risk_probability_updates": [{
                "fact_kind": "exported_binding_continuity",
                "provider": "materialized_codegraph",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner-b"],
                "original_probability": 0.45,
                "revised_probability": 0.1575,
                "probability_floor": 0.05,
            }],
        },
        "gates_fired": [{
            "name": "revision_risk_benefit_improved",
            "origin_expected_loss": 0.36,
            "revised_expected_loss": 0.08,
            "origin_rau": -0.06,
            "revised_rau": 0.22,
        }],
        "graph_refinement": {
            "status": "available",
            "selected": True,
            "evidence": {"facts": [{
                "fact_kind": "exported_binding_continuity",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner-a"],
            }]},
        },
    })

    summary = run_pair._graph_refinement_summary(result, "asm_graph")

    assert summary is not None
    assert summary["risk_probability_update_count"] == 0
    assert summary["proof_path"] is None


def test_graph_refinement_summary_rejects_duplicate_progress_gates():
    raw = {
        "scores": {
            "expected_loss": 0.08, "rau": 0.22,
            "risk_probability_updates": [{
                "fact_kind": "exported_binding_continuity",
                "provider": "materialized_codegraph", "event": "public_api_break",
                "risk_source": "graph_modify_risk", "owner_node_ids": ["owner-a"],
                "original_probability": 0.45, "revised_probability": 0.16,
                "probability_floor": 0.05,
            }],
        },
        "gates_fired": [
            {"name": "revision_risk_benefit_improved", "origin_expected_loss": 0.36,
             "revised_expected_loss": 0.08, "origin_rau": -0.1, "revised_rau": 0.22},
            {"name": "revision_risk_benefit_improved", "origin_expected_loss": 0.36,
             "revised_expected_loss": 0.30, "origin_rau": -0.1, "revised_rau": -0.2},
        ],
        "graph_refinement": {"status": "available", "selected": True, "evidence": {
            "facts": [{"fact_kind": "exported_binding_continuity",
                       "event": "public_api_break", "risk_source": "graph_modify_risk",
                       "owner_node_ids": ["owner-a"]}],
        }},
    }

    summary = run_pair._graph_refinement_summary(SimpleNamespace(raw_payload=raw), "asm_graph")

    assert summary is not None
    assert summary["revision_risk_benefit_improved"] is False
    assert summary["proof_path"] is None


def test_graph_refinement_summary_rejects_duplicate_verification_gates():
    raw = {
        "scores": {
            "expected_loss": 0.08,
            "rau": 0.22,
            "risk_probability_updates": [{
                "fact_kind": "exported_binding_continuity",
                "provider": "materialized_codegraph",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner-a"],
                "original_probability": 0.45,
                "revised_probability": 0.16,
                "probability_floor": 0.05,
            }],
        },
        "gates_fired": [
            {
                "name": "revision_risk_benefit_improved",
                "origin_expected_loss": 0.36,
                "revised_expected_loss": 0.08,
                "origin_rau": -0.1,
                "revised_rau": 0.22,
            },
            {"name": "candidate_verification_passed"},
            {"name": "candidate_verification_passed"},
        ],
        "graph_refinement": {
            "status": "available",
            "selected": True,
            "evidence": {"facts": [{
                "fact_kind": "exported_binding_continuity",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner-a"],
            }]},
        },
    }

    summary = run_pair._graph_refinement_summary(
        SimpleNamespace(raw_payload=raw), "asm_graph"
    )

    assert summary is not None
    assert summary["proof_path"] is None


def test_gate_binds_older_exact_assessment_not_latest_assessment(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry(
        last_assessment_id="asm_9",
        graph_refinement_by_assessment={
            "asm_7": {"assessment_id": "asm_7", "status": "available"},
            "asm_9": {"assessment_id": "asm_9", "status": "ambiguous"},
        },
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "schema_version": 2,
            "permission": "allow",
            "tier": "consulted",
            "reason": None,
            "warn": None,
            "risk_summary": {
                "decision": "proceed", "expected_loss": 0.08, "benefit": 0.65, "rau": 0.22,
            },
            "matched_assessment_id": "asm_7",
        },
    )

    result = run_pair._gate_check_backend(
        models.ARM_PEBRA, tmp_path / "pebra.db", telemetry=telemetry
    )({"tool_name": "Write"})

    assert telemetry.applied_assessment_id is None
    run_pair._write_applied_backend(telemetry)(result)
    assert telemetry.applied_assessment_id == "asm_7"
    assert telemetry.applied_graph_refinement == {
        "assessment_id": "asm_7", "status": "available"
    }
    assert "matched_assessment_id" not in result


def test_later_unbound_write_invalidates_graph_refinement_attribution() -> None:
    telemetry = run_pair.ArmTelemetry(
        applied_assessment_id="asm_graph",
        applied_graph_refinement={"assessment_id": "asm_graph", "status": "available"},
    )

    run_pair._write_applied_backend(telemetry)({"permission": "allow", "tier": "pass"})

    assert telemetry.applied_assessment_id is None
    assert telemetry.applied_graph_refinement is None
    assert telemetry.candidate_lineage_invalidated is True


def test_later_matched_write_does_not_erase_lineage_invalidation() -> None:
    telemetry = run_pair.ArmTelemetry(
        required_checks_by_assessment={"asm_new": ("targeted_tests",)},
        graph_refinement_by_assessment={
            "asm_new": {"assessment_id": "asm_new", "status": "available"}
        },
    )
    record = run_pair._write_applied_backend(telemetry)

    record({"permission": "allow", "tier": "pass"})
    record({"_matched_assessment_id": "asm_new"})

    assert telemetry.applied_assessment_id == "asm_new"
    assert telemetry.applied_required_checks == ("targeted_tests",)
    assert telemetry.candidate_lineage_invalidated is True


def test_later_differently_assessed_write_invalidates_candidate_lineage() -> None:
    telemetry = run_pair.ArmTelemetry(
        graph_refinement_by_assessment={
            "asm_a": {"assessment_id": "asm_a", "status": "available"},
            "asm_b": {"assessment_id": "asm_b", "status": "available"},
        },
    )
    record = run_pair._write_applied_backend(telemetry)

    record({"_matched_assessment_id": "asm_a"})
    record({"_matched_assessment_id": "asm_b"})

    assert telemetry.applied_assessment_id == "asm_b"
    assert telemetry.candidate_lineage_invalidated is True


def test_required_checks_bind_without_graph_refinement() -> None:
    telemetry = run_pair.ArmTelemetry(
        required_checks_by_assessment={"asm_plain": ("candidate_build",)},
    )

    run_pair._write_applied_backend(telemetry)({"_matched_assessment_id": "asm_plain"})

    assert telemetry.applied_graph_refinement is None
    assert telemetry.applied_required_checks == ("candidate_build",)


def test_denied_candidate_is_not_bound_for_post_edit_verify(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry(last_assessment_id="asm_7")
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "schema_version": 2,
            "permission": "deny", "tier": "consulted_revise", "reason": "revise",
            "warn": None,
            "risk_summary": {
                "decision": "revise_safer", "expected_loss": 0.61,
                "benefit": 0.34, "rau": -0.27,
            },
            "matched_assessment_id": "asm_7",
        },
    )

    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA, tmp_path / "pebra.db", telemetry=telemetry,
    )
    gate({"tool_name": "Write", "tool_input": {"file_path": "a.cs"}})

    assert telemetry.applied_assessment_id is None


def test_fail_open_write_is_not_bound_for_post_edit_verify(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry(last_assessment_id="asm_7")
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "schema_version": 2,
            "permission": "allow", "tier": "fail_open", "reason": None,
            "warn": "graph unavailable", "risk_summary": None,
            "matched_assessment_id": None,
        },
    )

    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA, tmp_path / "pebra.db", telemetry=telemetry,
    )
    gate({"tool_name": "Write", "tool_input": {"file_path": "a.cs"}})

    assert telemetry.applied_assessment_id is None


def test_post_edit_verify_persists_measured_benefit_for_applied_candidate(monkeypatch, tmp_path):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(
            applied_assessment_id="asm_7",
            applied_required_checks=("run targeted tests for the touched scope before commit",),
        ),
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
        tool_calls=(models.ToolCallRecord(
            sequence=1,
            name="run_tests",
            result={
                "available": True,
                "passed": True,
                "targeted": True,
                "tests_selected": 1,
            },
        ),),
    )
    seen = {}

    def _verify(assessment_id, *, repo_root, db, scope, completed_checks):
        seen.update({
            "assessment_id": assessment_id, "repo_root": repo_root, "db": db, "scope": scope,
            "completed_checks": completed_checks,
        })
        return True, {
            "measured_benefit": 0.42,
            "measured_benefit_deltas": {
                "complexity_delta": -2.0, "maintainability_index_delta": 4.0,
            },
        }

    monkeypatch.setattr(run_pair.cli_harness, "verify", _verify)

    verified = run_pair._post_edit_verify(setup, result)

    assert seen == {
        "assessment_id": "asm_7",
        "repo_root": tmp_path,
        "db": tmp_path.parent / "pebra.db",
        "scope": "all",
        "completed_checks": {
            "run targeted tests for the touched scope before commit": "passed"
        },
    }
    assert verified.post_edit_verify_ran is True
    assert verified.post_edit_verify_passed is True
    assert verified.post_edit_verify_assessment_id == "asm_7"
    assert verified.measured_benefit == pytest.approx(0.42)
    assert verified.measured_benefit_deltas == {
        "complexity_delta": -2.0, "maintainability_index_delta": 4.0,
    }


def test_post_edit_verify_uses_remaining_shared_run_deadline(monkeypatch, tmp_path):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(applied_assessment_id="asm_7"),
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
    )
    seen = {}

    def _verify(*_args, timeout, **_kwargs):
        seen["timeout"] = timeout
        return True, {}

    monkeypatch.setattr(run_pair.cli_harness, "verify", _verify)
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: 95.0)

    verified = run_pair._post_edit_verify(setup, result, deadline_monotonic=100.0)

    assert 0 < seen["timeout"] <= 5.0
    assert verified.post_edit_verify_ran is True
    assert verified.post_edit_verify_passed is True


def test_post_edit_verify_uses_only_its_allocated_closeout_budget(monkeypatch, tmp_path):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(applied_assessment_id="asm_7"),
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
    )
    seen = {}

    def _verify(*_args, timeout, **_kwargs):
        seen["timeout"] = timeout
        return True, {}

    monkeypatch.setattr(run_pair.cli_harness, "verify", _verify)
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: 50.0)

    verified = run_pair._post_edit_verify(
        setup,
        result,
        deadline_monotonic=100.0,
        verify_budget_seconds=10.0,
    )

    assert seen["timeout"] == pytest.approx(10.0)
    assert verified.post_edit_verify_passed is True


def test_post_edit_verify_fails_closed_when_allocated_budget_is_below_cli_floor(
    monkeypatch, tmp_path
):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(applied_assessment_id="asm_7"),
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: 99.5)

    verified = run_pair._post_edit_verify(
        setup,
        result,
        deadline_monotonic=100.0,
        verify_budget_seconds=10.0,
    )

    assert verified.post_edit_verify_ran is False
    assert verified.post_edit_verify_passed is False
    assert "insufficient" in verified.post_edit_verify_error


def test_post_edit_verify_fails_closed_when_shared_deadline_is_exhausted(monkeypatch, tmp_path):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(applied_assessment_id="asm_7"),
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: 100.0)

    verified = run_pair._post_edit_verify(setup, result, deadline_monotonic=100.0)

    assert verified.post_edit_verify_ran is False
    assert verified.post_edit_verify_passed is False
    assert "shared run deadline" in verified.post_edit_verify_error


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_build", result={"available": True, "passed": True}
                ),
            ),
            {},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": False,
                        "targeted": True, "tests_selected": 1,
                    }
                ),
            ),
            {"run targeted tests for the touched scope before commit": "failed"},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": True,
                        "targeted": True, "tests_selected": 1,
                    }
                ),
                models.ToolCallRecord(
                    2, "run_tests", result={
                        "available": True, "passed": False,
                        "targeted": True, "tests_selected": 1,
                    }
                ),
            ),
            {"run targeted tests for the touched scope before commit": "failed"},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": True,
                        "targeted": True, "tests_selected": 1,
                    }
                ),
                models.ToolCallRecord(2, "edit_file", result={"ok": False}),
            ),
            {"run targeted tests for the touched scope before commit": "passed"},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": True,
                        "targeted": True, "tests_selected": 1,
                    }
                ),
                models.ToolCallRecord(2, "edit_file", result={"ok": True}),
            ),
            {},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": True,
                        "targeted": False, "tests_selected": 10,
                    },
                ),
            ),
            {},
        ),
        (
            (
                models.ToolCallRecord(0, "edit_file", result={"ok": True}),
                models.ToolCallRecord(
                    1, "run_tests", result={
                        "available": True, "passed": True,
                        "targeted": True, "tests_selected": 0,
                    },
                ),
            ),
            {},
        ),
    ],
)
def test_post_edit_verify_uses_exact_checks_from_final_mutation_epoch(
    monkeypatch, tmp_path, calls, expected,
):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
        telemetry=run_pair.ArmTelemetry(
            applied_assessment_id="asm_7",
            applied_required_checks=(
                "run targeted tests for the touched scope before commit",
            ),
        ),
    )
    seen = {}

    def _verify(*args, completed_checks, **kwargs):
        seen.update(completed_checks)
        return False, {}

    monkeypatch.setattr(run_pair.cli_harness, "verify", _verify)
    result = SubjectResult(
        task_id="T1",
        arm=models.ARM_PEBRA,
        seed=0,
        modified_files=("a.cs",),
        tool_calls=calls,
    )

    run_pair._post_edit_verify(setup, result)

    assert seen == expected


def test_post_edit_verify_skips_when_no_exact_candidate_was_applied(monkeypatch, tmp_path):
    setup = run_pair.ArmSetup(
        arm=models.ARM_PEBRA,
        repo_path=tmp_path,
        advisory_backend=lambda payload: {},
        baseline_build=None,
        subject_prompt="x",
    )
    result = SubjectResult(
        task_id="T1", arm=models.ARM_PEBRA, seed=0, modified_files=("a.cs",),
    )
    monkeypatch.setattr(
        run_pair.cli_harness, "verify",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )

    verified = run_pair._post_edit_verify(setup, result)

    assert verified.post_edit_verify_ran is False


def test_real_advisory_backend_threads_revise_attempts(monkeypatch, tmp_path):
    attempts: list[int] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, max_revise_safer_attempts=1):
        attempts.append((revise_safer_attempt, max_revise_safer_attempts))
        return {
            "recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "reject",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)

    backend = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "pebra.db")
    payload = {
        "target_file": "src/A.cs",
        "change_summary": "edit",
        "proposed_patch": "diff --git a/src/A.cs b/src/A.cs",
    }
    backend(payload)
    backend(payload)

    # plain pebra arm: attempts advance 0 -> 1, cap stays 1
    assert attempts == [(0, 1), (1, 1)]


def test_real_advisory_captures_each_graph_scope_without_model_leak(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry()
    scopes = iter(("a" * 64, None))

    def _advise(payload, **kwargs):
        del payload, kwargs
        digest = next(scopes)
        raw = {
            "recommended_decision": "proceed",
            "scores": {},
            "graph_provenance": {"graph_scope_digest": digest},
        }
        return run_pair.advisory_check_real.AdvisoryOutput(
            run_pair.advisory_check_real._shape_output(raw),
            assessment_id="asm_7",
            raw_payload=raw,
        )

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )
    payload = {"target_file": "a.ts", "proposed_patch": "diff"}

    first = backend(payload)
    second = backend(payload)
    record = ToolCallRecord(0, "advisory_check", payload, first)

    assert telemetry.real_advisory_graph_scope_digests == ["a" * 64, None]
    assert tuple(first) == advisory_contract.OUTPUT_KEYS
    assert tuple(second) == advisory_contract.OUTPUT_KEYS
    assert "graph_scope" not in str(record.result)


@pytest.mark.parametrize(
    "arm",
    (models.ARM_CONTROL, models.ARM_SHAM, models.ARM_ORACLE_POSITIVE),
)
def test_non_real_advisories_do_not_invent_graph_scope_receipts(arm, tmp_path):
    telemetry = run_pair.ArmTelemetry()
    backend = run_pair._advisory_backend(
        arm,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )

    result = backend({"target_file": "a.ts", "proposed_patch": "diff"})

    assert tuple(result) == advisory_contract.OUTPUT_KEYS
    assert telemetry.real_advisory_graph_scope_digests == []


def test_subject_result_carries_host_graph_scope_receipts(monkeypatch, tmp_path):
    setup = _dummy_setup(tmp_path)
    setup.arm = models.ARM_TREATMENT
    setup.telemetry.real_advisory_graph_scope_digests.extend(("a" * 64, "a" * 64))
    monkeypatch.setenv("E2E_AB_RUN", "1")
    monkeypatch.setenv("E2E_EXTERNAL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(run_pair, "_load_config", lambda: {
        "subject": {
            "model": "test-model",
            "max_tool_calls_per_run": 5,
            "max_wall_seconds_per_run": 10,
            "max_output_tokens_per_turn": 100,
            "tools": ["read_file"],
        }
    })
    monkeypatch.setattr(model_client, "AnthropicClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        agent_loop,
        "run",
        lambda setup, spec, seed, **_kwargs: SubjectResult(
            task_id=spec.task_id, arm=setup.arm, seed=seed
        ),
    )
    monkeypatch.setattr(
        evaluator,
        "run_evaluator",
        lambda _repo, _spec: (
            SimpleNamespace(ran=True, passed=True, error_summary=""),
            SimpleNamespace(ran=True, passed=True, error_summary=""),
            False,
        ),
    )
    monkeypatch.setattr(evaluator, "run_completion_test", lambda *_args, **_kwargs: None)

    result = run_pair._invoke_subject_agent(setup, _SPEC, 0)

    assert result.real_advisory_graph_scope_digests == ("a" * 64, "a" * 64)


def test_real_advisory_backend_counts_cross_file_resubmission_per_run(monkeypatch, tmp_path):
    seen: list[tuple[str, int, int]] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, max_revise_safer_attempts=1):
        seen.append((payload["target_file"], revise_safer_attempt, max_revise_safer_attempts))
        return {
            "recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "reject",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    backend = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "pebra.db")

    backend({"target_file": "packages/zod/src/v3/types.ts", "proposed_patch": "risky"})
    backend({"target_file": "packages/zod/src/v3/helpers/util.ts", "proposed_patch": "safer"})

    assert seen == [
        ("packages/zod/src/v3/types.ts", 0, 1),
        ("packages/zod/src/v3/helpers/util.ts", 1, 1),
    ]


def test_repair_arm_verifies_cross_file_resubmission(monkeypatch, tmp_path):
    seen: list[dict] = []
    verified_targets: list[str] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, max_revise_safer_attempts=1):
        seen.append({
            "target": payload["target_file"],
            "attempt": revise_safer_attempt,
            "cap": max_revise_safer_attempts,
            "cv": payload.get("candidate_verification"),
        })
        return {
            "recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "proceed",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    def _verify(payload, repo_path, spec):
        verified_targets.append(payload["target_file"])
        return {
            "status": "passed",
            "required_checks": ["covering_tests"],
            "verified_patch_hash": "host",
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(run_pair, "_verify_candidate_for_repair", _verify)
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )

    backend({"target_file": "packages/zod/src/v3/types.ts", "proposed_patch": "risky"})
    backend({"target_file": "packages/zod/src/v3/helpers/util.ts", "proposed_patch": "safer"})

    assert seen[0]["attempt"] == 0 and seen[0]["cv"] is None
    assert seen[1]["attempt"] == 1 and seen[1]["cap"] == 2
    assert seen[1]["cv"]["status"] == "passed"
    assert verified_targets == ["packages/zod/src/v3/helpers/util.ts"]


def test_revise_attempt_state_is_isolated_and_bounded_per_backend(monkeypatch, tmp_path):
    attempts: list[tuple[str, int]] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, **kwargs):
        attempts.append((str(db), revise_safer_attempt))
        return {
            "recommended_decision": "revise_safer",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    first = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "first.db")
    second = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "second.db")
    payload = {"target_file": "a.ts", "proposed_patch": "diff"}

    first(payload)
    first(payload)
    first(payload)
    second(payload)

    assert attempts == [
        (str(tmp_path / "first.db"), 0),
        (str(tmp_path / "first.db"), 1),
        (str(tmp_path / "first.db"), 1),
        (str(tmp_path / "second.db"), 0),
    ]


def test_real_advisory_backend_threads_task_benefit_profile(monkeypatch, tmp_path):
    seen = {}
    spec = TaskSpec(
        "JS1", "d", ("a.ts",), "risky", ("a.ts",), "build_failure", True,
        assay_p_success=0.85, assay_immediate_benefit=0.65, assay_review_cost=0.05,
    )

    def _advise(payload, **kwargs):
        seen.update(kwargs)
        return {"recommended_decision": "proceed", "risk_level": "low", "advisory": "x", "detail": {}}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    backend = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "p.db", spec=spec)

    backend({"target_file": "a.ts", "proposed_patch": "diff", "change_summary": "x"})

    assert seen["p_success"] == pytest.approx(0.85)
    assert seen["immediate_benefit"] == pytest.approx(0.65)
    assert seen["review_cost"] == pytest.approx(0.05)


def test_real_advisory_backend_omits_priors_in_shipped_mode(monkeypatch, tmp_path):
    seen = {}
    spec = TaskSpec(
        "JS4", "d", ("a.ts",), "risky", ("a.ts",), "test_failure", False,
        assay_p_success=0.85, assay_immediate_benefit=0.65, assay_review_cost=0.05,
    )
    monkeypatch.setenv("E2E_AB_PRIOR_MODE", "shipped")

    def _advise(payload, **kwargs):
        seen.update(kwargs)
        return {"recommended_decision": "proceed", "risk_level": "low", "advisory": "x", "detail": {}}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    backend = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "p.db", spec=spec)

    backend({"target_file": "a.ts", "proposed_patch": "diff", "change_summary": "x"})

    assert seen["p_success"] is None
    assert seen["review_cost"] is None
    assert seen["immediate_benefit"] == pytest.approx(0.65)


@pytest.mark.parametrize(
    ("reassessment_decision", "reassessment_risk_mode"),
    [("proceed", "controlled_high_risk"), ("ask_human", "sensitive_context")],
)
def test_human_review_arm_requires_model_request_before_host_sanction_and_reassessment(
    monkeypatch, tmp_path, reassessment_decision, reassessment_risk_mode,
):
    calls: list[dict] = []
    approvals: list[dict] = []

    def _output(decision: str, *, assessment_id: str, risk_mode: str):
        raw = {
            "recommended_decision": decision,
            "risk_mode": risk_mode,
            "assessment_id": assessment_id,
            "scores": {
                "expected_loss": 0.36,
                "benefit": 0.50,
                "expected_utility": 0.14,
                "rau": 0.08,
            },
            "high_risk_triggers": [{"trigger_id": "public_contract"}],
            "model_guidance_packet": {
                "binding": {
                    "candidate": {
                        "algorithm": "sha256-normalized-content-v1",
                        "files": {"src/api.ts": "bound"},
                    },
                    "required_controls": ["human_review", "targeted_tests"],
                }
            },
            "next_action": {
                "type": "request_human_approval",
                "assessment_id": assessment_id,
                "action_id": "ab1",
                "candidate_binding": {
                    "algorithm": "sha256-normalized-content-v1",
                    "files": {"src/api.ts": "bound"},
                },
                "risk_benefit": {
                    "expected_loss": 0.36,
                    "benefit": 0.50,
                    "expected_utility": 0.14,
                    "rau": 0.08,
                },
                "required_controls": ["human_review", "targeted_tests"],
                "trusted_actor_required": True,
            },
        }
        return run_pair.advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": decision,
                "risk_level": "high",
                "advisory": "review",
                "detail": {},
            },
            assessment_id=assessment_id,
            raw_payload=raw,
        )

    def _advise(_payload, **kwargs):
        calls.append(dict(kwargs))
        return (
            _output("ask_human", assessment_id="asm_1", risk_mode="elevated_review")
            if len(calls) == 1
            else _output(
                reassessment_decision,
                assessment_id="asm_2",
                risk_mode=reassessment_risk_mode,
            )
        )

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(
        run_pair.cli_harness,
        "accept_risk",
        lambda spec, **kwargs: approvals.append({"spec": spec, **kwargs}) or {"sanction_id": "sx_1"},
    )
    telemetry = run_pair.ArmTelemetry()

    def _gate_check(_event, **_kwargs):
        if not telemetry.post_approval_reassessment:
            return {
                "schema_version": 2,
                "permission": "deny",
                "tier": "consulted_review_unavailable",
                "reason": "The original exact candidate remains held pending reassessment.",
                "warn": None,
                "risk_summary": {
                    "decision": "ask_human", "expected_loss": 0.36,
                    "benefit": 0.50, "rau": 0.08,
                },
                "matched_assessment_id": "asm_1",
            }
        if reassessment_decision == "proceed":
            return {
                "schema_version": 2,
                "permission": "allow",
                "tier": "consulted",
                "reason": None,
                "warn": None,
                "risk_summary": {
                    "decision": "proceed", "expected_loss": 0.08,
                    "benefit": 0.50, "rau": 0.08,
                },
                "matched_assessment_id": "asm_2",
            }
        return {
            "schema_version": 2,
            "permission": "deny",
            "tier": "consulted_review_unavailable",
            "reason": "The reassessed exact candidate remains held.",
            "warn": None,
            "risk_summary": {
                "decision": "ask_human", "expected_loss": 0.36,
                "benefit": 0.50, "rau": 0.08,
            },
            "matched_assessment_id": "asm_2",
        }

    monkeypatch.setattr(run_pair.cli_harness, "gate_check", _gate_check)
    advisory_backend = run_pair._advisory_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )

    approval_backend = run_pair._approval_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )
    payload = {
        "target_file": "src/api.ts",
        "change_summary": "preserve compatibility",
        "proposed_patch": "diff --git a/src/api.ts b/src/api.ts",
    }

    first = advisory_backend(payload)

    assert first["recommended_decision"] == "ask_human"
    assert len(calls) == 1
    assert approvals == []
    assert telemetry.human_approval_offered is True
    assert telemetry.human_approval_requested is False

    approval = approval_backend({"reason": "The remaining risk needs human acceptance."})

    assert approval["status"] == "approved"
    assert approvals[0]["spec"]["assessment_id"] == "asm_1"
    assert approvals[0]["spec"]["action_id"] == "ab1"
    assert approvals[0]["spec"]["pre_edit_authorization_controls_satisfied"] is True
    assert approvals[0]["spec"]["converts_gates"] == [2, 3, 4, 9]
    assert telemetry.human_approval_requested is True
    assert telemetry.human_approval_granted is True

    gate_backend = run_pair._gate_check_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )
    write_setup = SimpleNamespace(
        repo_path=tmp_path,
        gate_check_backend=gate_backend,
        write_applied_backend=run_pair._write_applied_backend(telemetry),
    )
    held = agent_loop._gated_write(
        {"path": "src/api.ts", "content": "before reassessment"}, write_setup
    )

    assert held["blocked"] is True
    assert telemetry.applied_assessment_id is None
    assert not (tmp_path / "src/api.ts").exists()

    second = advisory_backend(payload)

    assert second["recommended_decision"] == reassessment_decision
    assert len(calls) == 2
    assert telemetry.post_approval_reassessment is True
    assert telemetry.human_approval_assessment_id == "asm_1"
    assert telemetry.human_approval_source == "pre_registered_host_policy"

    after_reassessment = agent_loop._gated_write(
        {"path": "src/api.ts", "content": "after reassessment"}, write_setup
    )
    if reassessment_decision == "proceed":
        assert after_reassessment == {"ok": True, "blocked": False, "reason": None}
        assert telemetry.applied_assessment_id == "asm_2"
    else:
        assert after_reassessment["blocked"] is True
        assert telemetry.applied_assessment_id is None


def test_post_approval_obligation_revision_cannot_request_the_same_approval_again(
    monkeypatch, tmp_path
) -> None:
    binding = {
        "algorithm": "sha256-normalized-content-v1",
        "files": {"src/api.ts": "a" * 64},
    }
    first_raw = {
        "recommended_decision": "ask_human",
        "risk_mode": "elevated_review",
        "assessment_id": "asm_1",
        "scores": {"expected_loss": 0.36, "benefit": 0.50, "rau": 0.08},
        "next_action": {
            "type": "request_human_approval",
            "assessment_id": "asm_1",
            "action_id": "ab1",
            "candidate_binding": binding,
            "risk_benefit": {},
            "required_controls": [],
            "trusted_actor_required": True,
        },
    }
    second_raw = {
        "recommended_decision": "revise_safer",
        "risk_mode": "revise",
        "assessment_id": "asm_2",
        "scores": {"expected_loss": 0.36, "benefit": 0.50, "rau": 0.08},
        "gates_fired": [
            {"gate": 10, "name": "sanction_resolution"},
            {
                "gate": 15,
                "name": "task_obligations_incomplete",
                "missing_files": ["src/compat.ts"],
            },
        ],
        "model_guidance_packet": {
            "advisory": {
                "safer_route": {
                    "constraints": [
                        "Include the host-required files in the candidate: src/compat.ts."
                    ]
                }
            }
        },
    }
    outputs = iter((first_raw, second_raw))

    def _advise(_payload, **_kwargs):
        raw = next(outputs)
        return run_pair.advisory_check_real.AdvisoryOutput(
            run_pair.advisory_check_real._shape_output(raw),
            assessment_id=raw["assessment_id"],
            raw_payload=raw,
        )

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(
        run_pair.cli_harness,
        "accept_risk",
        lambda *_args, **_kwargs: {"sanction_id": "sx_1"},
    )
    telemetry = run_pair.ArmTelemetry()
    advisory = run_pair._advisory_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )
    approval = run_pair._approval_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry,
    )
    payload = {
        "target_file": "src/api.ts",
        "change_summary": "preserve compatibility",
        "proposed_patch": "diff --git a/src/api.ts b/src/api.ts",
    }

    assert advisory(payload)["recommended_decision"] == "ask_human"
    assert approval({"reason": "accept risk"})["status"] == "approved"
    revised = advisory(payload)

    assert revised["recommended_decision"] == "revise_safer"
    assert "src/compat.ts" in revised["advisory"]
    assert telemetry.post_approval_reassessment is True
    assert telemetry.pending_human_approval is None
    unavailable = approval({"reason": "approve the unchanged candidate again"})
    assert unavailable == {
        "status": "unavailable",
        "approval_id": None,
        "message": "No approvable exact candidate is pending; revise the candidate or stop.",
    }


def test_host_approval_policy_fails_closed_without_canonical_bound_request() -> None:
    shaped_only = {
        "recommended_decision": "ask_human",
        "risk_level": "high",
        "advisory": "review",
        "detail": {},
    }
    reject = run_pair.advisory_check_real.AdvisoryOutput(
        shaped_only,
        assessment_id="asm_1",
        raw_payload={
            "recommended_decision": "reject",
            "next_action": {"type": "stop"},
        },
    )
    unbound = run_pair.advisory_check_real.AdvisoryOutput(
        shaped_only,
        assessment_id="asm_1",
        raw_payload={
            "recommended_decision": "ask_human",
            "next_action": {
                "type": "request_human_approval",
                "assessment_id": "asm_1",
                "action_id": "ab1",
                "candidate_binding": None,
                "trusted_actor_required": True,
            },
        },
    )

    assert run_pair._human_approval_spec(shaped_only) is None
    assert run_pair._human_approval_spec(reject) is None
    assert run_pair._human_approval_spec(unbound) is None


def test_human_approval_backend_denies_without_creating_sanction(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("E2E_AB_HUMAN_APPROVAL_POLICY", "deny")
    accepted = []
    monkeypatch.setattr(
        run_pair.cli_harness,
        "accept_risk",
        lambda *args, **kwargs: accepted.append((args, kwargs)),
    )
    telemetry = run_pair.ArmTelemetry(
        human_approval_offered=True,
        pending_human_approval={"assessment_id": "asm_1"},
    )
    backend = run_pair._approval_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry,
    )

    result = backend({"reason": "review the residual risk"})

    assert result["status"] == "denied"
    assert telemetry.human_approval_requested is True
    assert telemetry.human_approval_granted is False
    assert accepted == []


def test_human_approval_backend_is_unavailable_without_pending_candidate(tmp_path) -> None:
    telemetry = run_pair.ArmTelemetry()
    backend = run_pair._approval_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path,
        tmp_path / "pebra.db",
        telemetry,
    )

    result = backend({"reason": "review the residual risk"})

    assert result["status"] == "unavailable"
    assert telemetry.human_approval_requested is True
    assert telemetry.human_approval_granted is False


def test_approval_unavailable_output_is_identical_across_arms(tmp_path) -> None:
    outputs = []
    for arm in models.ALL_ASSAY_ARMS:
        telemetry = run_pair.ArmTelemetry()
        outputs.append(
            run_pair._approval_backend(
                arm, tmp_path, tmp_path / f"{arm}.db", telemetry
            )({"reason": "review residual risk"})
        )

    assert all(output == outputs[0] for output in outputs)


def test_human_review_gate_records_write_attempt_before_approval(monkeypatch, tmp_path) -> None:
    telemetry = run_pair.ArmTelemetry(human_approval_offered=True)
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, **_kwargs: {
            "schema_version": 2,
            "permission": "deny", "tier": "consulted_review_unavailable", "reason": "wait",
            "warn": None,
            "risk_summary": {
                "decision": "ask_human", "expected_loss": 0.61,
                "benefit": 0.34, "rau": -0.27,
            },
            "matched_assessment_id": "asm_1",
        },
    )
    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )

    result = gate({
        "tool_name": "Write",
        "tool_input": {"file_path": "src/api.ts", "content": "unsafe"},
        "cwd": str(tmp_path),
    })

    assert result["permission"] == "deny"
    assert telemetry.write_before_approval is True
    assert telemetry.write_before_reassessment is True


def test_human_assisted_write_attribution_is_sticky_across_later_candidate(
    monkeypatch, tmp_path
) -> None:
    telemetry = run_pair.ArmTelemetry(
        human_approval_granted=True,
        post_approval_reassessment=True,
        approved_reassessment_id="asm_2",
        last_assessment_id="asm_2",
    )
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, **_kwargs: {
            "schema_version": 2,
            "permission": "allow", "tier": "consulted",
            "reason": None, "warn": None,
            "risk_summary": {
                "decision": "proceed", "expected_loss": 0.08, "benefit": 0.65, "rau": 0.22,
            },
            "matched_assessment_id": "asm_2",
        },
    )
    gate = run_pair._gate_check_backend(
        models.ARM_PEBRA_HUMAN_REVIEW,
        tmp_path / "pebra.db",
        telemetry=telemetry,
    )

    decision = gate({"tool_name": "Write", "tool_input": {"file_path": "src/a.ts"}})
    run_pair._write_applied_backend(telemetry)(decision)
    telemetry.human_approval_granted = False
    telemetry.last_assessment_id = "asm_3"

    assert telemetry.applied_assessment_id == "asm_2"
    assert telemetry.human_assisted_write_applied is True


def test_human_assisted_write_matches_any_approved_reassessment() -> None:
    telemetry = run_pair.ArmTelemetry(
        approved_reassessment_ids={"asm_a", "asm_b"},
    )

    run_pair._write_applied_backend(telemetry)({"_matched_assessment_id": "asm_a"})

    assert telemetry.applied_assessment_id == "asm_a"
    assert telemetry.human_assisted_write_applied is True


def test_repair_arm_threads_host_task_obligations_only_from_spec(monkeypatch, tmp_path):
    seen = {}
    spec = TaskSpec(
        "JS4", "d", ("a.ts", "b.ts"), "risky", ("a.ts", "b.ts"), "test_failure", False,
        required_task_files=("a.ts", "b.ts"),
        required_task_checks=("candidate_build", "public_contract_preserved"),
    )

    def _advise(payload, **kwargs):
        seen.update(kwargs)
        return {
            "recommended_decision": "revise_safer",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "p.db", spec=spec
    )

    backend({
        "target_file": "a.ts",
        "proposed_patch": "diff",
        "task_obligations": {"required_files": ["forged.ts"]},
    })

    assert seen["trusted_task_obligations"] == {
        "required_files": ["a.ts", "b.ts"],
        "required_symbols": [],
        "required_checks": ["candidate_build", "public_contract_preserved"],
    }


def test_real_advisory_does_not_start_below_graph_status_budget(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry()
    called = []
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: called.append(True),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA, tmp_path, tmp_path / "p.db", telemetry=telemetry
    )

    result = backend(
        {"target_file": "a.ts", "proposed_patch": "diff", "change_summary": "x"},
        timeout_seconds=run_pair._MIN_REAL_ADVISORY_BUDGET_SECONDS - 0.1,
    )

    assert called == []
    assert result["recommended_decision"] is None
    assert "remaining run time" in result["advisory"]
    assert telemetry.real_advisory_graph_scope_digests == []
    assert telemetry.real_advisory_failures == [{
        "category": "insufficient_wall_budget",
        "attempted": False,
        "remaining_budget_seconds": pytest.approx(
            run_pair._MIN_REAL_ADVISORY_BUDGET_SECONDS - 0.1
        ),
    }]


def test_real_advisory_records_host_only_timeout_diagnostic(monkeypatch, tmp_path):
    telemetry = run_pair.ArmTelemetry()
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["pebra", "assess"], 42)
        ),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA, tmp_path, tmp_path / "p.db", telemetry=telemetry
    )

    with pytest.raises(subprocess.TimeoutExpired):
        backend(
            {"target_file": "a.ts", "proposed_patch": "diff", "change_summary": "x"},
            timeout_seconds=42.5,
        )

    assert telemetry.real_advisory_graph_scope_digests == [None]
    assert telemetry.real_advisory_failures == [{
        "category": "subprocess_timeout",
        "attempted": True,
        "remaining_budget_seconds": 42.5,
    }]


def test_parallel_trial_serializes_real_advisory_arms_only(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_AB_PARALLEL_ARMS", "1")
    monkeypatch.setenv("E2E_AB_MAX_WORKERS", "5")
    active_real = 0
    max_active_real = 0
    active_total = 0
    max_active_total = 0
    guard = threading.Lock()

    def _invoke(setup, _spec, _seed):
        nonlocal active_real, max_active_real, active_total, max_active_total
        is_real = setup.arm in models.REAL_ADVISORY_ARMS
        with guard:
            active_total += 1
            max_active_total = max(max_active_total, active_total)
            if is_real:
                active_real += 1
                max_active_real = max(max_active_real, active_real)
        time.sleep(0.05)
        with guard:
            active_total -= 1
            if is_real:
                active_real -= 1
        return SubjectResult(task_id="T1", arm=setup.arm, seed=0)

    monkeypatch.setattr(run_pair, "_invoke_trial_setup", _invoke)
    setups = [
        _dummy_setup(tmp_path),
        replace(_dummy_setup(tmp_path), arm=models.ARM_PEBRA),
        replace(_dummy_setup(tmp_path), arm=models.ARM_PEBRA_GRAPH_REPAIR),
        replace(_dummy_setup(tmp_path), arm=models.ARM_PEBRA_HUMAN_REVIEW),
    ]

    results = run_pair._invoke_trial_setups(setups, _SPEC, 0)

    assert {result.arm for result in results} == {setup.arm for setup in setups}
    assert max_active_real == 1
    assert max_active_total > 1


def test_repair_arm_verifies_candidate_and_raises_cap(monkeypatch, tmp_path):
    # P4: the graph_repair arm host-produces candidate_verification on the narrowed resubmit (attempt>=1)
    # and raises the cap to 2 so gate 7 is reachable. Verification pieces are monkeypatched (no dotnet).
    seen: list[dict] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, max_revise_safer_attempts=1):
        seen.append({"attempt": revise_safer_attempt, "cap": max_revise_safer_attempts,
                     "cv": payload.get("candidate_verification")})
        return {"recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "proceed",
                "risk_level": "high", "advisory": "x", "detail": {}}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(run_pair, "_verify_candidate_for_repair",
                        lambda payload, repo_path, spec: {"status": "passed",
                        "required_checks": ["covering_tests"], "verified_patch_hash": "h"})

    backend = run_pair._advisory_backend(
        run_pair.models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db")
    payload = {"target_file": "src/A.cs", "change_summary": "e", "proposed_patch": "diff x"}
    backend(payload)  # attempt 0: revise_safer, no verification yet
    backend(payload)  # attempt 1: verified candidate injected, cap=2

    assert seen[0]["cap"] == 2 and seen[0]["cv"] is None            # cap raised; no verify on 1st call
    assert seen[1]["attempt"] == 1 and seen[1]["cv"]["status"] == "passed"  # host-produced evidence injected


def test_repair_verification_time_is_subtracted_before_assess(monkeypatch, tmp_path):
    received_timeouts: list[float | None] = []

    def _advise(payload, **kwargs):
        received_timeouts.append(kwargs.get("timeout_seconds"))
        return {
            "recommended_decision": "revise_safer" if len(received_timeouts) == 1 else "proceed",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    ticks = iter([0.0, 0.0, 60.0])
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: next(ticks, 60.0))
    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(
        run_pair,
        "_verify_candidate_for_repair",
        lambda *_args, **_kwargs: {
            "status": "passed",
            "required_checks": ["covering_tests"],
            "verified_patch_hash": "host",
        },
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )
    payload = {"target_file": "src/A.ts", "proposed_patch": "patch"}

    backend(payload)
    backend(payload, timeout_seconds=100.0)

    assert received_timeouts == [None, pytest.approx(40.0)]


def test_repair_arm_does_not_verify_after_revision_budget_is_exhausted(monkeypatch, tmp_path):
    attempts: list[int] = []
    verified: list[str] = []

    def _advise(payload, *, revise_safer_attempt=0, **_kwargs):
        attempts.append(revise_safer_attempt)
        return {
            "recommended_decision": "revise_safer",
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    def _verify(payload, _repo_path, _spec):
        verified.append(payload["proposed_patch"])
        return {"status": "failed"}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(run_pair, "_verify_candidate_for_repair", _verify)
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )

    backend({"target_file": "a.ts", "proposed_patch": "initial"})
    backend({"target_file": "a.ts", "proposed_patch": "revision-1"})
    backend({"target_file": "a.ts", "proposed_patch": "revision-after-cap"})

    assert attempts == [0, 1, 2]
    assert verified == ["revision-1"]


def test_repair_unavailable_verification_preserves_verified_candidate_budget(monkeypatch, tmp_path):
    seen: list[dict] = []
    verifications = iter((
        {"status": "unavailable", "reason": "codegraph timed out",
         "retryable_infrastructure": True},
        {"status": "passed", "required_checks": ["covering_tests"],
         "checks": {"covering_tests": "passed"}, "verified_patch_hash": "bound"},
    ))

    def _advise(payload, *, revise_safer_attempt=0, **_kwargs):
        seen.append({
            "attempt": revise_safer_attempt,
            "verification": payload.get("candidate_verification"),
        })
        verification = payload.get("candidate_verification") or {}
        return {
            "recommended_decision": (
                "proceed" if verification.get("status") == "passed" else "revise_safer"
            ),
            "risk_level": "high",
            "advisory": "x",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(
        run_pair, "_verify_candidate_for_repair", lambda *_args, **_kwargs: next(verifications)
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )

    backend({"target_file": "a.ts", "proposed_patch": "initial"})
    backend({"target_file": "a.ts", "proposed_patch": "unparseable revision"})
    backend({"target_file": "a.ts", "proposed_patch": "correct wrapper"})

    assert [entry["attempt"] for entry in seen] == [0, 1, 1]
    assert [entry["verification"]["status"] for entry in seen[1:]] == [
        "unavailable",
        "passed",
    ]


def test_nonretryable_prevalidation_failures_consume_semantic_attempt_budget(monkeypatch, tmp_path):
    attempts: list[int] = []
    verifications = iter([
        {"status": "unavailable", "failure_category": "patch_not_applicable"}
        for _ in range(4)
    ] + [{"status": "passed", "verified_patch_hash": "bound"}])

    def _advise(payload, *, revise_safer_attempt=0, **_kwargs):
        attempts.append(revise_safer_attempt)
        verification = payload.get("candidate_verification") or {}
        return {
            "recommended_decision": (
                "proceed" if verification.get("status") == "passed" else "revise_safer"
            ),
            "risk_level": "high",
            "advisory": "retry",
            "detail": {},
        }

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(
        run_pair, "_verify_candidate_for_repair", lambda *_args, **_kwargs: next(verifications)
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )

    backend({"target_file": "a.ts", "proposed_patch": "initial"})
    for index in range(4):
        result = backend({"target_file": "a.ts", "proposed_patch": f"bad-{index}"})
        if index == 0:
            assert "could not be applied" in result["advisory"]
    final = backend({"target_file": "a.ts", "proposed_patch": "valid"})

    assert attempts == [0, 1, 2, 2, 2, 2]
    assert final["recommended_decision"] != "proceed"


def test_real_backend_materializes_structured_edits_before_assess_and_verify(monkeypatch, tmp_path):
    assessed: list[str] = []
    verified: list[str] = []
    canonical = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"

    monkeypatch.setattr(
        run_pair.cli_harness,
        "candidate_patch",
        lambda edits, *, repo_root, timeout=120: {
            "proposed_patch": canonical,
            "expected_files": ["a.ts"],
        },
    )

    def _advise(payload, *, revise_safer_attempt=0, **_kwargs):
        assessed.append(payload["proposed_patch"])
        return {
            "recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "proceed",
            "risk_level": "high",
            "advisory": "retry",
            "detail": {},
        }

    def _verify(payload, *_args, **_kwargs):
        verified.append(payload["proposed_patch"])
        return {"status": "passed", "verified_patch_hash": "bound"}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(run_pair, "_verify_candidate_for_repair", _verify)
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )
    payload = {
        "target_file": "a.ts",
        "change_summary": "preserve compatibility",
        "candidate_edits": [{"path": "a.ts", "old_string": "old", "new_string": "new"}],
    }

    first = backend(payload)
    second = backend(payload)

    assert assessed == [canonical, canonical]
    assert verified == [canonical]
    patch_id = advisory_contract.candidate_patch_id(canonical)
    assert first["detail"] == {"candidate_patch_id": patch_id}
    assert second["detail"] == {"candidate_patch_id": patch_id}


def test_structured_candidate_patch_handoff_is_identical_across_real_and_sham_arms(
    monkeypatch, tmp_path
):
    canonical = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"
    payload = {
        "target_file": "a.ts",
        "change_summary": "rename helper",
        "candidate_edits": [
            {"path": "a.ts", "old_string": "old", "new_string": "new"}
        ],
    }
    monkeypatch.setattr(
        run_pair.cli_harness,
        "candidate_patch",
        lambda edits, *, repo_root, timeout=120: {
            "proposed_patch": canonical,
            "expected_files": ["a.ts"],
        },
    )
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda payload, **_kwargs: {
            "recommended_decision": "proceed",
            "risk_level": "low",
            "advisory": "ok",
            "detail": {},
        },
    )

    real = run_pair._advisory_backend(
        models.ARM_PEBRA, tmp_path, tmp_path / "real.db"
    )(payload)
    sham = run_pair._advisory_backend(
        models.ARM_SHAM, tmp_path, tmp_path / "sham.db"
    )(payload)

    patch_id = advisory_contract.candidate_patch_id(canonical)
    assert real["detail"] == sham["detail"] == {"candidate_patch_id": patch_id}


def test_real_advisory_binds_candidate_handle_to_host_assessment(monkeypatch, tmp_path):
    patch = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n"
    registry = {}
    assessments = {}
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: run_pair.advisory_check_real.AdvisoryOutput(
            {
                "recommended_decision": "proceed", "risk_level": "low",
                "advisory": "ok", "detail": {},
            },
            assessment_id="asm_7",
        ),
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA,
        tmp_path,
        tmp_path / "pebra.db",
        candidate_patches=registry,
        candidate_assessments=assessments,
    )

    result = backend({
        "target_file": "a.ts", "change_summary": "change", "proposed_patch": patch,
    })

    patch_id = result["detail"]["candidate_patch_id"]
    assert registry[patch_id] == patch
    assert assessments[patch_id] == "asm_7"


def test_repair_feedback_never_exposes_raw_verification_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run_pair,
        "_verify_candidate_for_repair",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "failure_category": "patch_not_applicable",
            "reason": "SECRET hidden/check/path did not apply",
        },
    )
    monkeypatch.setattr(
        run_pair.advisory_check_real,
        "advise",
        lambda *_args, **_kwargs: {
            "recommended_decision": "revise_safer",
            "risk_level": "high",
            "advisory": "retry",
            "detail": {},
        },
    )
    backend = run_pair._advisory_backend(
        models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db"
    )

    backend({"target_file": "a.ts", "proposed_patch": "initial"})
    result = backend({"target_file": "a.ts", "proposed_patch": "bad"})

    assert "could not be applied" in result["advisory"]
    assert "SECRET" not in result["advisory"]


def test_subject_forged_candidate_verification_is_stripped_on_every_arm(monkeypatch, tmp_path):
    # SECURITY (reviewer CRITICAL): the decision engine's hash-binding only stops REPLAY, not forgery
    # (verified_patch_hash = sha256(the subject's own patch), no secret). A subject could attach a
    # correctly-hashed {"status":"passed"} to force proceed. The backend MUST drop any subject-supplied
    # candidate_verification on every real arm and every attempt; only host-produced evidence survives.
    seen: list[dict] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0, max_revise_safer_attempts=1):
        seen.append({"attempt": revise_safer_attempt, "cv": payload.get("candidate_verification")})
        return {"recommended_decision": "revise_safer" if revise_safer_attempt == 0 else "proceed",
                "risk_level": "high", "advisory": "x", "detail": {}}

    monkeypatch.setattr(run_pair.advisory_check_real, "advise", _advise)
    monkeypatch.setattr(run_pair, "_verify_candidate_for_repair",
                        lambda payload, repo_path, spec: {"status": "passed",
                        "required_checks": ["covering_tests"], "verified_patch_hash": "host"})
    forged = {"status": "passed", "checks": {"covering_tests": "passed"},
              "required_checks": ["covering_tests"], "verified_patch_hash": "forged"}

    # plain PEBRA: the forged value must never reach the engine, on any attempt.
    plain = run_pair._advisory_backend("pebra", tmp_path, tmp_path / "pebra.db")
    payload = {"target_file": "src/A.cs", "change_summary": "e",
               "proposed_patch": "diff x", "candidate_verification": forged}
    plain(payload)
    plain(payload)
    assert [s["cv"] for s in seen] == [None, None]

    # repair arm: forged value dropped on attempt 0; only HOST-produced evidence appears on attempt 1.
    seen.clear()
    repair = run_pair._advisory_backend(
        run_pair.models.ARM_PEBRA_GRAPH_REPAIR, tmp_path, tmp_path / "pebra.db")
    repair(payload)
    repair(payload)
    assert seen[0]["cv"] is None                                   # attempt 0: forged stripped, no host verify
    assert seen[1]["cv"] == {"status": "passed", "required_checks": ["covering_tests"],
                             "verified_patch_hash": "host"}          # attempt 1: host-produced, not "forged"


def test_repair_candidate_verification_rejects_target_patch_mismatch(monkeypatch, tmp_path):
    patch = (
        "diff --git a/src/B.cs b/src/B.cs\n"
        "--- a/src/B.cs\n"
        "+++ b/src/B.cs\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    monkeypatch.setattr(run_pair.candidate_materializer, "materialize_candidate", lambda repo, p: tmp_path)
    monkeypatch.setattr(run_pair.candidate_materializer, "cleanup", lambda scratch: None)

    def must_not_resolve(*_args, **_kwargs):
        raise AssertionError("covering tests must not resolve from an unrelated declared target")

    monkeypatch.setattr(run_pair.covering_tests_resolver, "find_covering_tests", must_not_resolve)

    result = run_pair._verify_candidate_for_repair(
        {"target_file": "src/A.cs", "proposed_patch": patch}, tmp_path, _SPEC
    )

    assert result["status"] == "unavailable"
    assert result["failure_category"] == "target_mismatch"
    assert "target" in result["reason"]


def test_repair_candidate_verification_accepts_atomic_multifile_candidate(monkeypatch, tmp_path):
    patch = (
        "diff --git a/src/A.ts b/src/A.ts\n--- a/src/A.ts\n+++ b/src/A.ts\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/B.ts b/src/B.ts\n--- a/src/B.ts\n+++ b/src/B.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(run_pair.candidate_materializer, "materialize_candidate", lambda *_args: scratch)
    monkeypatch.setattr(run_pair.candidate_materializer, "cleanup", lambda _scratch: None)
    monkeypatch.setattr(
        run_pair.covering_tests_resolver,
        "find_covering_tests",
        lambda _repo, target, _patch, **_kwargs: ("tests", target),
    )
    seen = {}
    monkeypatch.setattr(
        run_pair.candidate_verifier,
        "verify_candidate",
        lambda **kwargs: seen.update(kwargs) or {"status": "passed"},
    )
    spec = replace(_SPEC, language="typescript", harness_id="node")

    result = run_pair._verify_candidate_for_repair(
        {"target_file": "src/A.ts", "proposed_patch": patch}, tmp_path, spec
    )

    assert result["status"] == "passed"
    assert seen["repo_path"] == scratch
    assert seen["test_project"] is None  # conflicting per-file selectors use the full-build fallback
    assert seen["allow_build_fallback"] is True


def test_repair_candidate_verification_prefers_declared_public_test_selector(
    monkeypatch, tmp_path
):
    patch = (
        "diff --git a/src/A.ts b/src/A.ts\n"
        "--- a/src/A.ts\n+++ b/src/A.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(
        run_pair.candidate_materializer, "materialize_candidate", lambda *_args: scratch
    )
    monkeypatch.setattr(run_pair.candidate_materializer, "cleanup", lambda _scratch: None)
    monkeypatch.setattr(
        run_pair.covering_tests_resolver,
        "find_covering_tests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("declared public test must not fall back to graph resolution")
        ),
    )
    seen = {}
    monkeypatch.setattr(
        run_pair.candidate_verifier,
        "verify_candidate",
        lambda **kwargs: seen.update(kwargs) or {"status": "passed"},
    )
    spec = replace(
        _SPEC,
        language="typescript",
        harness_id="node",
        test_selector="packages/zod/src/v3/tests/error.test.ts",
        required_task_checks=("candidate_build", "public_contract_preserved"),
    )

    result = run_pair._verify_candidate_for_repair(
        {"target_file": "src/A.ts", "proposed_patch": patch}, tmp_path, spec
    )

    assert result["status"] == "passed"
    assert seen["test_project"] == "packages/zod/src/v3/tests/error.test.ts"
    assert seen["allow_build_fallback"] is True
    assert seen["required_checks"] == ("candidate_build", "public_contract_preserved")


def test_repair_candidate_verification_records_stage_timings(monkeypatch, tmp_path):
    patch = "diff --git a/src/A.ts b/src/A.ts\n--- a/src/A.ts\n+++ b/src/A.ts\n@@ -1 +1 @@\n-old\n+new\n"
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(run_pair.candidate_materializer, "materialize_candidate", lambda *_: scratch)
    monkeypatch.setattr(run_pair.candidate_materializer, "cleanup", lambda *_: None)
    monkeypatch.setattr(
        run_pair.covering_tests_resolver,
        "find_covering_tests",
        lambda *_args, **_kwargs: ("src/A.test.ts", None),
    )
    monkeypatch.setattr(
        run_pair.candidate_verifier,
        "verify_candidate",
        lambda **_kwargs: {"status": "passed", "provenance": {"tests_selected": 1}},
    )
    ticks = iter((10.0, 11.0, 12.5, 15.0))
    monkeypatch.setattr(run_pair.time, "monotonic", lambda: next(ticks))

    result = run_pair._verify_candidate_for_repair(
        {"target_file": "src/A.ts", "proposed_patch": patch}, tmp_path,
        replace(_SPEC, language="typescript", harness_id="node"),
    )

    assert result["provenance"] == {
        "tests_selected": 1,
        "materialize_seconds": 1.0,
        "resolve_seconds": 1.5,
        "verification_seconds": 2.5,
    }


def test_repair_normalizes_verifier_unavailability_to_safe_category(monkeypatch, tmp_path):
    patch = "diff --git a/src/A.ts b/src/A.ts\n--- a/src/A.ts\n+++ b/src/A.ts\n@@ -1 +1 @@\n-old\n+new\n"
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(run_pair.candidate_materializer, "materialize_candidate", lambda *_: scratch)
    monkeypatch.setattr(run_pair.candidate_materializer, "cleanup", lambda *_: None)
    monkeypatch.setattr(
        run_pair.covering_tests_resolver,
        "find_covering_tests",
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        run_pair.candidate_verifier,
        "verify_candidate",
        lambda **_kwargs: {"status": "unavailable", "reason": "runner unavailable"},
    )

    result = run_pair._verify_candidate_for_repair(
        {"target_file": "src/A.ts", "proposed_patch": patch},
        tmp_path,
        replace(_SPEC, language="typescript", harness_id="node"),
    )

    assert result["failure_category"] == "verification_unavailable"


def test_prepare_arm_fails_closed_on_bad_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 700})
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend(
        SimpleNamespace(available=True, ran=True, passed=False, error_summary="baseline broken")
    ))

    with pytest.raises(run_pair.RunPairError, match="baseline"):
        run_pair.prepare_arm(_External(), _SPEC, "control", 0, "rid")


def test_prepare_arm_does_not_apply_csharp_floor_to_tiered_multilanguage_task(monkeypatch, tmp_path):
    spec = TaskSpec(
        "TSSEM", "d", ("src/a.ts",), "safe", ("src/a.ts",), "none", False,
        required_language_tier="full",
    )
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 0})
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend())

    setup = run_pair.prepare_arm(_External(), spec, "pebra", 0, "rid")

    assert setup.arm == "pebra"
    assert setup.spec is spec
    assert setup.build_backend is not None


def test_prepare_arm_uses_language_backend_for_baseline(monkeypatch, tmp_path):
    spec = TaskSpec(
        "JS1", "d", ("src/a.ts",), "safe", ("src/a.ts",), "none", False,
        required_language_tier="full", language="typescript",
    )
    seen = {}
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 0})

    def _record(repo_path, spec_arg, baseline_keys):
        seen["language"] = spec_arg.language

    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec_arg: _FakeBackend(recorder=_record))

    setup = run_pair.prepare_arm(_External(), spec, "pebra", 0, "rid")

    assert seen == {"language": "typescript"}
    assert setup.spec is spec


def test_prepare_arm_still_applies_csharp_floor_to_legacy_graph_task(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 0})

    with pytest.raises(run_pair.RunPairError, match="C# callable nodes"):
        run_pair.prepare_arm(_External(), _SPEC, "pebra", 0, "rid")


def test_oracle_positive_pre_patch_happens_before_baseline_build(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    calls: list[str] = []

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        return dest

    def _patch(repo_path, task_id, **_kwargs):
        calls.append(f"patch:{task_id}:{repo_path.name}")
        patch = repo_path / "patch.diff"
        patch.write_text("diff --git a/a.cs b/a.cs\n", encoding="utf-8")
        return patch

    def _record(repo_path, spec, baseline_keys):
        calls.append(f"build:{repo_path.name}:{spec.build_solution}")

    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(arm_prep, "prepare_oracle_patch", _patch)
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend(recorder=_record))

    setup = run_pair.prepare_arm(_External(), _SPEC, "oracle_positive", 0, "rid")

    assert setup.arm == "oracle_positive"
    assert calls == ["patch:T1:repo", "build:repo:TemplateBlueprint.sln"]


def test_oracle_positive_uses_specimen_correct_fix_dir(monkeypatch, tmp_path):
    spec = TaskSpec(
        "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
        ("packages/zod/src/v3/types.ts",), "build_failure", True,
        build_solution="", language="typescript", harness_id="node", specimen="javascript",
    )
    seen = {}
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    def _patch(repo_path, task, **kw):
        seen.setdefault("patch_dir", kw["patch_dir"])
        patch = repo_path / f"{task}.patch"
        patch.write_text(
            "diff --git a/packages/zod/src/v3/types.ts b/packages/zod/src/v3/types.ts\n",
            encoding="utf-8",
        )
        return patch

    monkeypatch.setattr(arm_prep, "prepare_oracle_patch", _patch)
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec_arg: _FakeBackend())

    run_pair.prepare_arm(_External(), spec, "oracle_positive", 0, "rid")

    assert seen["patch_dir"].as_posix().endswith("specimens/javascript/corpus/correct_fix_patches")


def test_prepare_arm_passes_task_build_solution_to_baseline(monkeypatch, tmp_path):
    spec = TaskSpec(
        "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
        build_solution="MathNet.Numerics.sln",
    )
    seen = {}
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 700})

    def _record(repo_path, spec, baseline_keys):
        seen["sln"] = spec.build_solution

    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend(recorder=_record))

    setup = run_pair.prepare_arm(_External(), spec, "sham", 0, "rid")

    assert setup.build_solution == "MathNet.Numerics.sln"
    assert seen["sln"] == "MathNet.Numerics.sln"


def test_subject_prompt_lists_all_served_tools_and_advisory_workflow(tmp_path):
    prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "sham")
    for name in ("read_file", "write_file", "edit_file", "apply_patch", "list_dir", "search_grep", "run_build",
                 "run_tests", "advisory_check"):
        assert name in prompt
    assert subject_protocol.INSTRUCTION_REL_PATH in prompt
    assert "read" in prompt.lower()
    assert "before every repository file creation, edit, rename, or deletion" in prompt.lower()
    assert "intended patch" in prompt.lower()
    lower = prompt.lower()
    assert "recommended_decision=reject" not in lower
    assert "revise_safer" not in lower


def test_pebra_arm_prompt_points_to_file_without_embedding_safe_edit_protocol(tmp_path):
    prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "pebra")

    assert subject_protocol.INSTRUCTION_REL_PATH in prompt
    assert "Safe-edit protocol" not in prompt
    assert "resubmit a narrower candidate" not in prompt
    assert "write only after" not in prompt.lower()


def test_pebra_and_sham_prompts_are_identical_except_task_fields(tmp_path):
    pebra_prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "pebra")
    sham_prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "sham")

    assert pebra_prompt == sham_prompt


def test_prepare_arm_writes_blinded_protocol_file_for_every_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 700})
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend())

    pebra = run_pair.prepare_arm(_External(), _SPEC, "pebra", 0, "rid")
    sham = run_pair.prepare_arm(_External(), _SPEC, "sham", 0, "rid")

    pebra_protocol = (pebra.repo_path / subject_protocol.INSTRUCTION_REL_PATH).read_text(encoding="utf-8")
    sham_protocol = (sham.repo_path / subject_protocol.INSTRUCTION_REL_PATH).read_text(encoding="utf-8")
    assert "resubmit a safer or compatibility-preserving candidate" in pebra_protocol
    assert "alias" in pebra_protocol
    assert "wrapper" in pebra_protocol
    assert "candidate_edits" in pebra_protocol
    assert "candidate_edits" in sham_protocol
    assert "Draft the intended patch" in sham_protocol


def test_real_advisory_arm_membership_has_one_source_of_truth():
    assert run_pair._REAL_ADVISORY_ARMS == models.REAL_ADVISORY_ARMS
    for arm in models.REAL_ADVISORY_ARMS:
        protocol = subject_protocol.protocol_for_arm(arm)
        assert "resubmit a safer or compatibility-preserving candidate" in protocol
        assert "alias" in protocol
        assert "wrapper" in protocol


def test_subject_prompt_does_not_include_absolute_repo_path_or_engine_name(tmp_path):
    repo = tmp_path / "pebra" / "e2e" / "out" / "repo"
    prompt = run_pair._build_subject_prompt(_SPEC, repo, "pebra")
    assert str(repo) not in prompt
    assert "pebra" not in prompt.lower()


def test_subject_prompt_uses_task_language(tmp_path):
    spec = TaskSpec(
        "JS1", "d", ("src/a.ts",), "safe", ("src/a.ts",), "none", False, language="typescript",
    )
    prompt = run_pair._build_subject_prompt(spec, tmp_path, "sham")
    assert "TypeScript codebase" in prompt
    assert "C# codebase" not in prompt
