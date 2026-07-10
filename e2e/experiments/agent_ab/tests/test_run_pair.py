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
    subject_protocol,
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
    monkeypatch.setattr(run_pair.backends, "backend_for_spec", lambda spec: _FakeBackend())
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
    assert "target" in result["reason"]


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
        return repo_path / "patch.diff"

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
    monkeypatch.setattr(arm_prep, "prepare_oracle_patch",
                        lambda repo_path, task, **kw: seen.setdefault("patch_dir", kw["patch_dir"]))
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
    for name in ("read_file", "write_file", "list_dir", "search_grep", "run_build", "run_tests",
                 "advisory_check"):
        assert name in prompt
    assert subject_protocol.INSTRUCTION_REL_PATH in prompt
    assert "read" in prompt.lower()
    assert "before significant edits" in prompt.lower()
    assert "intended patch" in prompt.lower()
    lower = prompt.lower()
    assert "if advisory_check returns recommended_decision=reject" in lower
    assert "do not edit" in lower


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
    assert "resubmit a narrower candidate" in pebra_protocol
    assert "Draft the intended patch" in sham_protocol


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
