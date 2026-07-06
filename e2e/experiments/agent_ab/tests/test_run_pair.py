"""Pin the fail-closed gate INSIDE the agent-invocation path. This guards the safety check across
refactors: if the check_gate() call at the top of _invoke_subject_agent were removed, these fail. Now
that Phase G has removed the AnthropicClient.send NotImplementedError stop, the gate is the SOLE guard,
so this pin is the last line of defence against an accidental live run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab.models import SubjectResult, TaskSpec
from e2e.experiments.agent_ab.runners import (
    agent_loop, arm_prep, evaluator, model_client, run_control, run_gate, run_pair,
)

_SPEC = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)


def _dummy_setup(tmp_path):
    return run_pair.ArmSetup(
        arm="control", repo_path=tmp_path, advisory_backend=lambda payload: {},
        baseline_build=None, subject_prompt="do the task",
    )


class _External:
    source_path = None
    head_sha = "abc123"


def _close_gate(mp):
    mp.delenv("E2E_AB_RUN", raising=False)
    mp.delenv("E2E_EXTERNAL", raising=False)
    mp.delenv("ANTHROPIC_API_KEY", raising=False)


def test_invoke_subject_agent_gated_fail_closed(monkeypatch, tmp_path):
    _close_gate(monkeypatch)
    # The gate is the FIRST statement; it must raise before any config load / AnthropicClient / clone.
    with pytest.raises(run_gate.RunGateError):
        run_pair._invoke_subject_agent(_dummy_setup(tmp_path), _SPEC, 0)


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
    monkeypatch.setattr(agent_loop, "run", lambda setup, spec, seed, *, client, config: SubjectResult(
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
        def __init__(self, *, model, api_key, base_url=None):
            created["model"] = model
            created["api_key"] = api_key
            created["base_url"] = base_url

    monkeypatch.setenv("E2E_AB_RUN", "1")
    monkeypatch.setenv("E2E_EXTERNAL", "1")
    monkeypatch.setenv("E2E_AB_PROVIDER", "deepseek")
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
    monkeypatch.setattr(agent_loop, "run", lambda setup, spec, seed, *, client, config: SubjectResult(
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
    monkeypatch.setattr(run_pair.dn, "run_build_delta",
                        lambda repo, *, sln="TemplateBlueprint.sln": SimpleNamespace(
                            available=True, ran=True, passed=True, error_summary=""))
    stale = tmp_path / "rid" / f"T1_seed0_{run_pair._arm_token('treatment', 'rid')}" / "repo"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("stale")

    setup = run_pair.prepare_arm(_External(), _SPEC, "treatment", 0, "rid")

    assert setup.repo_path == stale
    assert not (stale / "old.txt").exists()
    assert (stale / "repo.txt").read_text() == "fresh"
    assert calls == [stale]


def test_treatment_gate_check_backend_uses_consult_only(monkeypatch, tmp_path):
    captured = {}

    def _gate_check(event, *, db, consult_only=False):
        captured["event"] = event
        captured["db"] = db
        captured["consult_only"] = consult_only
        return {"permission": "allow", "tier": "consulted"}

    monkeypatch.setattr(run_pair.cli_harness, "gate_check", _gate_check)

    db = tmp_path / "pebra.db"
    backend = run_pair._gate_check_backend("treatment", db)
    result = backend({"tool_name": "Write"})

    assert result == {"permission": "allow", "tier": "consulted"}
    assert captured == {
        "event": {"tool_name": "Write"},
        "db": db,
        "consult_only": True,
    }


def test_real_advisory_backend_threads_revise_attempts(monkeypatch, tmp_path):
    attempts: list[int] = []

    def _advise(payload, *, repo_root, db, revise_safer_attempt=0):
        attempts.append(revise_safer_attempt)
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

    assert attempts == [0, 1]


def test_prepare_arm_fails_closed_on_bad_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head",
                        lambda _external, dest: (dest.mkdir(parents=True), dest)[1])
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(run_pair.cli_harness, "graph_node_counts",
                        lambda *, repo_root: {"csharp_callable": 700})
    monkeypatch.setattr(run_pair.dn, "run_build_delta",
                        lambda repo, *, sln="TemplateBlueprint.sln": SimpleNamespace(
                            available=True, ran=True, passed=False, error_summary="baseline broken"))

    with pytest.raises(run_pair.RunPairError, match="baseline"):
        run_pair.prepare_arm(_External(), _SPEC, "control", 0, "rid")


def test_oracle_positive_pre_patch_happens_before_baseline_build(monkeypatch, tmp_path):
    monkeypatch.setattr(run_pair, "_AB_OUT", tmp_path)
    calls: list[str] = []

    def _clone(_external, dest):
        dest.mkdir(parents=True)
        return dest

    def _patch(repo_path, task_id):
        calls.append(f"patch:{task_id}:{repo_path.name}")
        return repo_path / "patch.diff"

    def _build(repo_path, *, sln="TemplateBlueprint.sln"):
        calls.append(f"build:{repo_path.name}:{sln}")
        return SimpleNamespace(available=True, ran=True, passed=True, error_summary="")

    monkeypatch.setattr(run_pair.rs, "clone_at_recorded_head", _clone)
    monkeypatch.setattr(run_pair.cli_harness, "setup_graph", lambda *, repo_root: None)
    monkeypatch.setattr(arm_prep, "prepare_oracle_patch", _patch)
    monkeypatch.setattr(run_pair.dn, "run_build_delta", _build)

    setup = run_pair.prepare_arm(_External(), _SPEC, "oracle_positive", 0, "rid")

    assert setup.arm == "oracle_positive"
    assert calls == ["patch:T1:repo", "build:repo:TemplateBlueprint.sln"]


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

    def _build(repo_path, *, sln="TemplateBlueprint.sln"):
        seen["sln"] = sln
        return SimpleNamespace(available=True, ran=True, passed=True, error_summary="")

    monkeypatch.setattr(run_pair.dn, "run_build_delta", _build)

    setup = run_pair.prepare_arm(_External(), spec, "sham", 0, "rid")

    assert setup.build_solution == "MathNet.Numerics.sln"
    assert seen["sln"] == "MathNet.Numerics.sln"


def test_subject_prompt_lists_all_served_tools_and_advisory_workflow(tmp_path):
    prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "sham")
    for name in ("read_file", "write_file", "list_dir", "search_grep", "run_build", "run_tests",
                 "advisory_check"):
        assert name in prompt
    assert "before significant edits" in prompt.lower()
    assert "intended patch" in prompt.lower()
    lower = prompt.lower()
    assert "if advisory_check returns recommended_decision=reject" in lower
    assert "do not edit" in lower


def test_pebra_arm_prompt_includes_blinded_safe_edit_skill(tmp_path):
    prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "pebra")

    assert "Safe-edit protocol" in prompt
    assert "resubmit a narrower candidate" in prompt
    assert "write only after" in prompt.lower()


def test_non_pebra_arm_prompt_gets_placebo_protocol(tmp_path):
    prompt = run_pair._build_subject_prompt(_SPEC, tmp_path, "sham")

    assert "Safe-edit protocol" not in prompt
    assert "Edit protocol" in prompt
    assert "Draft the intended patch" in prompt
    assert "resubmit a narrower candidate" not in prompt


def test_subject_prompt_does_not_include_absolute_repo_path_or_engine_name(tmp_path):
    repo = tmp_path / "pebra" / "e2e" / "out" / "repo"
    prompt = run_pair._build_subject_prompt(_SPEC, repo, "pebra")
    assert str(repo) not in prompt
    assert "pebra" not in prompt.lower()
