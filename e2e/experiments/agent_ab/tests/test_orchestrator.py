"""Orchestrator glue: plan order, atomic write, crash-survivable resume, and the end-to-end
SubjectResult -> score -> aggregate -> report path — all with fakes, no LLM / clone / dotnet."""

from __future__ import annotations

import json

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


def _wire(monkeypatch, tmp_path, corpus, run_pair_fn):
    monkeypatch.setattr(orchestrator, "_AB_OUT", tmp_path)
    monkeypatch.setattr(orchestrator.run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(orchestrator.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(orchestrator.loader, "load_corpus", lambda: corpus)
    monkeypatch.setattr(orchestrator, "_config",
                        lambda: {"pilot": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0})
    monkeypatch.setattr(orchestrator.run_pair, "run_pair", run_pair_fn)


def test_main_end_to_end_writes_report(monkeypatch, tmp_path):
    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    _wire(monkeypatch, tmp_path, [_T1], _fake_pair)
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "outcomes.json").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()
    assert (tmp_path / "t1" / "reports" / "ab_t1.json").exists()


def test_main_resumes_and_skips_completed_pair(monkeypatch, tmp_path):
    # Pre-seed a completed pair for (T1, 0); run_pair must NOT be called again.
    out_path = tmp_path / "t1" / "outcomes.json"
    orchestrator._write_outcomes(
        out_path, [_outcome("T1", models.ARM_CONTROL, 0), _outcome("T1", models.ARM_TREATMENT, 0)], "t1")

    def _must_not_run(spec, seed, run_id):
        raise AssertionError("run_pair called for an already-completed pair")

    _wire(monkeypatch, tmp_path, [_T1], _must_not_run)
    rc = orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])
    assert rc == 0
    assert (tmp_path / "t1" / "reports" / "ab_t1.md").exists()  # still renders from loaded outcomes


def test_scoring_mode_build_break_when_no_evaluator_tests():
    # No corpus/evaluator_tests/<id>/ dirs exist for these -> build_break_scope.
    assert orchestrator._scoring_mode([_T1, _B1]) == "build_break_scope"


def test_main_reports_scoring_mode_for_planned_tasks_only(monkeypatch, tmp_path):
    seen = {}

    def _fake_pair(spec, seed, run_id):
        return (SubjectResult(task_id=spec.task_id, arm=models.ARM_CONTROL, seed=seed),
                SubjectResult(task_id=spec.task_id, arm=models.ARM_TREATMENT, seed=seed))

    def _capture_report(*_args, **kwargs):
        seen["scoring_mode"] = kwargs["scoring_mode"]

    _wire(monkeypatch, tmp_path, [_T1, _B1], _fake_pair)
    monkeypatch.setattr(orchestrator, "_scoring_mode", lambda specs: ",".join(s.task_id for s in specs))
    monkeypatch.setattr(orchestrator.render_report, "write_report", _capture_report)

    orchestrator.main(["--run-id", "t1", "--skip-oracle-preflight", "--skip-graph-preflight"])

    assert seen["scoring_mode"] == "T1"
