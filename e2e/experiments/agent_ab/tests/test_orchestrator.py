"""Orchestrator glue: plan order, atomic write, crash-survivable resume, and the end-to-end
SubjectResult -> score -> aggregate -> report path — all with fakes, no LLM / clone / dotnet."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import RunOutcome, SubjectResult, TaskSpec
from e2e.experiments.agent_ab.runners import orchestrator

_T1 = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)
_B1 = TaskSpec("B1", "d", ("a.cs",), "safe", ("a.cs",), "none", False)


def _outcome(task_id: str, arm: str, seed: int = 0) -> RunOutcome:
    return RunOutcome(
        task_id=task_id, arm=arm, seed=seed, harm_label="risky", harm_materialized=False,
        task_completed=True, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=False, test_failed=False, edit_cycle_count=1, advisory_called=False,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def test_plan_is_sorted_and_seeded():
    plan = orchestrator._plan([_T1, _B1], ["B1", "T1"], 2)
    assert plan == [(_B1, 0), (_B1, 1), (_T1, 0), (_T1, 1)]


def test_plan_rejects_missing_tasks():
    try:
        orchestrator._plan([_T1], ["T1", "T9"], 1)
    except ValueError as exc:
        assert "T9" in str(exc)
    else:
        raise AssertionError("missing configured task must fail closed")


def test_orchestrator_corpus_loader_includes_javascript_specimen():
    corpus = orchestrator.load_corpus()
    by_id = {spec.task_id: spec for spec in corpus}
    assert "JS1" in by_id
    assert by_id["JS1"].specimen == "javascript"
    assert by_id["JS1"].harness_id == "node"


def test_live_assess_uses_specimen_oracle_patch(monkeypatch, tmp_path):
    spec = TaskSpec(
        "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
        ("packages/zod/src/v3/types.ts",), "build_failure", True,
        build_solution="", language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json", "pnpm-lock.yaml"),
    )
    seen = {}

    def _assess(req_path, *, repo_root, db):
        payload = json.loads(Path(req_path).read_text(encoding="utf-8"))
        seen["patch"] = payload["candidate_actions"][0]["proposed_patch"]
        return {"ok": True}

    monkeypatch.setattr(orchestrator.cli_harness, "assess", _assess)

    orchestrator._live_assess_fn(tmp_path, spec)

    assert "packages/zod/src/v3/types.ts" in seen["patch"]


def test_config_applies_model_override(monkeypatch):
    monkeypatch.setenv("E2E_AB_MODEL", "claude-haiku-4-5-20251001")
    cfg = orchestrator._config()
    assert cfg["subject"]["model"] == "claude-haiku-4-5-20251001"


def test_config_has_one_pair_smoke_mode():
    cfg = orchestrator._config()
    assert cfg["smoke"]["tasks"] == ["T1"]


def test_config_has_javascript_assay_mode():
    cfg = orchestrator._config()
    assert cfg["assay_js"]["tasks"] == ["JS1", "JS2", "JS3"]
    assert cfg["assay_js"]["seeds_per_arm"] == 3
    assert cfg["smoke"]["seeds_per_arm"] == 1
    assert cfg["smoke"]["total_runs"] == 2


def test_assay_config_does_not_expose_dead_arm_list():
    cfg = orchestrator._config()
    assert "arms" not in cfg["assay"]


def test_write_outcomes_atomic(tmp_path):
    path = tmp_path / "outcomes.json"
    orchestrator._write_outcomes(path, [_outcome("T1", models.ARM_CONTROL)], "rid")
    assert path.exists() and not path.with_suffix(".tmp").exists()
    payload = json.loads(path.read_text())
    assert payload["run_id"] == "rid" and len(payload["outcomes"]) == 1


def test_completed_pairs_requires_both_arms():
    outcomes = [
        _outcome("T1", models.ARM_CONTROL, 0), _outcome("T1", models.ARM_TREATMENT, 0),  # full
        _outcome("T1", models.ARM_CONTROL, 1),                                            # partial
    ]
    assert orchestrator._completed_pairs(outcomes) == {("T1", 0)}


def test_load_existing_outcomes_roundtrip(tmp_path):
    path = tmp_path / "outcomes.json"
    orchestrator._write_outcomes(path, [_outcome("T1", models.ARM_CONTROL)], "rid")
    loaded = orchestrator._load_existing_outcomes(path)
    assert len(loaded) == 1 and loaded[0].task_id == "T1"
    assert loaded[0].blinding_terms == ()  # list -> tuple on reload


def test_load_existing_outcomes_roundtrip_served_models(tmp_path):
    path = tmp_path / "outcomes.json"
    outcome = dataclasses.replace(_outcome("T1", models.ARM_TREATMENT), served_models=("m1", "m2"))
    orchestrator._write_outcomes(path, [outcome], "rid")
    loaded = orchestrator._load_existing_outcomes(path)
    assert loaded[0].served_models == ("m1", "m2")


def _wire(monkeypatch, tmp_path, corpus, run_pair_fn):
    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: corpus)
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"pilot": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.run_pair, "run_pair", run_pair_fn)


def test_main_end_to_end_writes_report(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "outcomes.json").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.json").exists()


def test_main_writes_finished_run_status(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    status = json.loads((tmp_path / "t1" / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "finished"
    assert status["mode"] == "pilot"
    assert status["run_id"] == "t1"
    assert status["updated_at"]  # an ISO timestamp is stamped
    assert status["run_metadata"]["git_commit"]
    assert status["run_metadata"]["provider"] == "anthropic"
    assert status["run_metadata"]["parallel_arms"] is False
    assert status["run_metadata"]["protocol_file"] == ".agent-instructions/edit_protocol.md"
    assert set(status["run_metadata"]["protocol_hashes"]) >= {"sham", "pebra"}


def test_main_resumes_and_skips_completed_pair(monkeypatch, tmp_path):
    # Pre-seed a completed pair for (T1, 0); run_pair must NOT be called again.
    out_path = tmp_path / "t1" / "outcomes.json"
    orchestrator._write_outcomes(
        out_path, [_outcome("T1", models.ARM_CONTROL, 0), _outcome("T1", models.ARM_TREATMENT, 0)], "t1")

    def _must_not_run(spec, seed, run_id):
        raise AssertionError("run_pair called for an already-completed pair")

    _wire(monkeypatch, tmp_path, [_T1], _must_not_run)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()  # still renders from loaded outcomes


def test_main_fails_fast_on_errored_run(monkeypatch, tmp_path):
    # A live-client error captured into SubjectResult.error must abort, not be silently scored.
    def _erroring_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed,
                              error="AuthenticationError: invalid x-api-key"),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _erroring_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    try:
        orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    except orchestrator.ExperimentRunError as exc:
        assert "invalid x-api-key" in str(exc)
    else:
        raise AssertionError("errored run must fail-fast, not be silently scored")
    assert not (tmp_path / "t1" / "outcomes.json").exists()  # aborted pair not written
    status = json.loads((tmp_path / "t1" / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "failed"
    assert "invalid x-api-key" in status["error"]


def test_scoring_mode_build_break_when_no_evaluator_tests():
    # No specimen evaluator_tests/<id>/ dirs exist for these -> build_break_scope.
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_break_scope"


def test_scoring_mode_requires_real_evaluator_project(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_EVAL_DIR", tmp_path / "eval")
    (tmp_path / "eval" / "T1").mkdir(parents=True)
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_break_scope"
    (tmp_path / "eval" / "T1" / "Evaluator.csproj").write_text("<Project />")
    assert orchestrator._scoring_mode([_T1, _B1]) == "mixed_build_test_scope"
    (tmp_path / "eval" / "B1").mkdir()
    (tmp_path / "eval" / "B1" / "Evaluator.csproj").write_text("<Project />")
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_test_scope"


def test_scoring_mode_treats_existing_repo_test_filter_as_test_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_EVAL_DIR", tmp_path / "eval")
    gamma = TaskSpec(
        "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
        evaluator_test_project="tests/Tests.csproj", evaluator_test_filter="FullyQualifiedName~GammaTests",
    )
    assert orchestrator._scoring_mode([gamma]) == "build_test_scope"


def test_main_reports_scoring_mode_for_planned_tasks_only(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    def _capture_report(*_args, **kwargs):
        seen["scoring_mode"] = kwargs["scoring_mode"]

    _wire(monkeypatch, tmp_path, [_T1, _B1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    monkeypatch.setattr(orchestrator, "_scoring_mode", lambda specs: ",".join(s.task_id for s in specs))
    monkeypatch.setattr(orchestrator.render_report, "write_report", _capture_report)

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])

    assert seen["scoring_mode"] == "T1"


def test_main_runs_preflights_for_planned_tasks_only(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1, _B1], _fake_pair)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda specs, *_args, **_kwargs: seen.setdefault("oracle", [s.task_id for s in specs]))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda specs, *_args, **_kwargs: seen.setdefault("graph", [s.task_id for s in specs]))

    orchestrator.main(["--run-id", "t1"])

    assert seen == {"oracle": ["T1"], "graph": ["T1"]}


def test_main_runs_revise_safer_calibration_for_assay(monkeypatch, tmp_path):
    gamma = TaskSpec(
        "MNGAMMA", "d", ("src/Gamma.cs",), "risky", ("src/Gamma.cs",), "test_failure", False,
        evaluator_test_project="tests/Tests.csproj", evaluator_test_filter="FullyQualifiedName~GammaTests",
        build_solution="MathNet.Numerics.sln",
    )
    calls: list[str] = []
    seen = {}

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [gamma])
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"assay": {"tasks": ["MNGAMMA"], "seeds_per_arm": 1},
                                 "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda *a, **k: calls.append("oracle"))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda *a, **k: calls.append("graph"))
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration",
                        lambda *a, **k: calls.append("revise"))
    monkeypatch.setattr(orchestrator.run_pair, "run_trial",
                        lambda spec, seed, run_id: tuple(
                            SubjectResult(task_id=spec.task_id, arm=arm, seed=seed)
                            for arm in orchestrator.run_pair.arms_for(spec.harm_label)
                        ))
    monkeypatch.setattr(orchestrator.render_report, "write_assay_report",
                        lambda *a, **k: seen.setdefault("preflight_status", k["preflight_status"]))

    orchestrator.main(["--run-id", "assay1", "--mode", "assay"])

    assert calls == ["oracle", "graph", "revise"]
    assert seen["preflight_status"]["revise_safer"] == "passed"


def test_preflight_only_js_assay_skips_paid_run_gate_and_subject(monkeypatch, tmp_path):
    js1 = TaskSpec(
        "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
        ("packages/zod/src/v3/types.ts",), "build_failure", True,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    js2 = TaskSpec(
        "JS2", "d", ("packages/zod/src/v3/types.ts",), "safe",
        ("packages/zod/src/v3/types.ts",), "none", False,
        language="typescript", harness_id="node", specimen="javascript",
        repo_identity_files=("package.json",),
    )
    calls: list[str] = []

    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate",
                        lambda: (_ for _ in ()).throw(AssertionError("paid gate must be skipped")))
    monkeypatch.setattr(orchestrator, "load_corpus", lambda: [js1, js2])
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"assay_js": {"tasks": ["JS1", "JS2"], "seeds_per_arm": 1},
                                 "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: tmp_path / "source")
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight",
                        lambda *a, **k: calls.append("identity"))
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight",
                        lambda *a, **k: calls.append("oracle"))
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight",
                        lambda *a, **k: calls.append("graph"))
    monkeypatch.setattr(orchestrator.preflight, "run_revise_safer_calibration",
                        lambda *a, **k: calls.append("revise"))
    monkeypatch.setattr(orchestrator.run_pair, "run_trial",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subject must not run")))

    assert orchestrator.main(["--run-id", "js-pf", "--mode", "assay_js", "--preflight-only"]) == 0
    assert calls == ["identity", "oracle", "graph", "revise"]


def test_preflight_only_rejects_preflight_skip_flags(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, [_T1], lambda spec, seed, run_id: (_outcome("T1", "control"),
                                                                   _outcome("T1", "treatment")))
    with pytest.raises(orchestrator.ExperimentRunError, match="preflight-only"):
        orchestrator.main([
            "--run-id", "pf",
            "--preflight-only",
            "--skip-oracle-preflight",
            "--skip-graph-preflight",
        ])


def test_main_runs_repo_identity_preflight_before_external_clone(monkeypatch, tmp_path):
    calls: list[str] = []

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(orchestrator.rs, "source_repo_path", lambda: source)

    def _identity(specs, source_root):
        calls.append("identity")
        assert source_root == source
        assert [s.task_id for s in specs] == ["T1"]

    def _prepare():
        calls.append("prepare")
        return object()

    monkeypatch.setattr(orchestrator.preflight, "run_repo_identity_preflight", _identity)
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", _prepare)
    monkeypatch.setattr(orchestrator.preflight, "run_oracle_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)

    orchestrator.main(["--run-id", "t1"])

    assert calls[:2] == ["identity", "prepare"]


def test_skip_preflight_requires_debug_env(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, [_T1], lambda spec, seed, run_id: (_outcome("T1", "control"),
                                                                   _outcome("T1", "treatment")))
    monkeypatch.delenv("E2E_AB_ALLOW_UNVERIFIED", raising=False)
    with pytest.raises(orchestrator.ExperimentRunError, match="E2E_AB_ALLOW_UNVERIFIED"):
        orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight"])


def test_skipped_preflight_status_is_reported(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    def _capture_report(*_args, **kwargs):
        seen["preflight_status"] = kwargs["preflight_status"]

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    monkeypatch.setenv("E2E_AB_ALLOW_UNVERIFIED", "1")
    monkeypatch.setattr(orchestrator.preflight, "run_graph_preflight", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.render_report, "write_report", _capture_report)

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight"])

    assert seen["preflight_status"]["oracle"] == "skipped"
    assert seen["preflight_status"]["graph"] == "passed"
