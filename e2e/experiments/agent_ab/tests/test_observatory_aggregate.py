"""Pure aggregation for the run observatory: turn a run dir's artifacts into the /api JSON view.

All logic here is I/O-light (read files, call the EXISTING e2e aggregators, assemble a dict). No pebra
import, no reimplementation of scorecard/plan/arm-token logic — those are imported from the producers.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import run_pair
from e2e.experiments.agent_ab.runners.observatory import aggregate


def _oc(task_id, arm, seed, *, harm_label="risky", harm=False, over_cautious=False, completed=True):
    return models.RunOutcome(
        task_id=task_id, arm=arm, seed=seed, harm_label=harm_label,
        harm_materialized=harm, task_completed=completed, over_cautious=over_cautious,
        quality_failure=False, scope_drift=False, build_failed=harm, test_failed=False,
        edit_cycle_count=1, advisory_called=(arm in (models.ARM_PEBRA, models.ARM_TREATMENT)),
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def _write_run(ab_out, run_id, outcomes, *, run_status=None, coverage=None, reports=False):
    run_dir = ab_out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    (run_dir / "outcomes.json").write_text(json.dumps(payload), encoding="utf-8")
    if run_status is not None:
        (run_dir / "run_status.json").write_text(json.dumps(run_status), encoding="utf-8")
    if coverage is not None:
        (run_dir / "preflight").mkdir(exist_ok=True)
        (run_dir / "preflight" / "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
    if reports:
        (run_dir / "reports").mkdir(exist_ok=True)
        (run_dir / "reports" / f"ab_{run_id}.json").write_text("{}", encoding="utf-8")
    return run_dir


# --- basic 2-arm (control/treatment) view -------------------------------------------------------


def test_build_run_view_basic_ab(tmp_path):
    outcomes = [dataclasses.replace(_oc("T1", models.ARM_CONTROL, 0, harm=True),
                                    protocol_file_read=True),
                _oc("T1", models.ARM_TREATMENT, 0, harm=False)]
    _write_run(tmp_path, "r1", outcomes)
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode="pilot",
                                    corpus=[TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",),
                                                     "build_failure", True)],
                                    config={"pilot": {"tasks": ["T1"], "seeds_per_arm": 1},
                                            "bootstrap_seed": 0})
    assert view["run_id"] == "r1"
    assert view["counts"]["done"] == 2          # two observed arm-cells (control + treatment)
    assert view["counts"]["total_planned"] == 2  # pilot plans control+treatment for T1 x seed0
    assert view["counts"]["pending"] == 0
    assert "net_benefit" in view["scoreboard"]["endpoints"]
    cells = {(m["task_id"], m["seed"], m["arm"]): m for m in view["matrix"]}
    assert cells[("T1", 0, models.ARM_CONTROL)]["status"] == "done"
    assert cells[("T1", 0, models.ARM_CONTROL)]["outcome_summary"]["harm_materialized"] is True
    assert cells[("T1", 0, models.ARM_CONTROL)]["outcome_summary"]["protocol_file_read"] is True


def test_mode_absent_gives_observed_only_matrix(tmp_path):
    outcomes = [_oc("T1", models.ARM_CONTROL, 0), _oc("T1", models.ARM_TREATMENT, 0)]
    _write_run(tmp_path, "r1", outcomes)
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode=None,
                                    corpus=[], config={"bootstrap_seed": 0})
    assert view["counts"]["total_planned"] is None
    assert view["counts"]["pending"] is None
    assert all(m["status"] == "done" for m in view["matrix"])


def test_mode_present_yields_pending_cells(tmp_path):
    # planned: T1 (risky, 6 arms) x seed 0 ; observed: only sham + pebra -> the rest are pending.
    outcomes = [_oc("T1", models.ARM_SHAM, 0), _oc("T1", models.ARM_PEBRA, 0)]
    _write_run(tmp_path, "r1", outcomes)
    corpus = [TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)]
    config = {"assay": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0}
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode="assay", corpus=corpus, config=config)
    assert view["counts"]["total_planned"] == len(run_pair.arms_for("risky"))
    pending = {(m["task_id"], m["seed"], m["arm"]) for m in view["matrix"] if m["status"] == "pending"}
    assert ("T1", 0, models.ARM_ORACLE_POSITIVE) in pending
    assert view["counts"]["pending"] == len(run_pair.arms_for("risky")) - 2


def test_pending_cells_keep_language_specimen_and_harm_label(tmp_path):
    _write_run(tmp_path, "r1", [])
    corpus = [
        TaskSpec(
            "JS1", "d", ("packages/zod/src/v3/types.ts",), "risky",
            ("packages/zod/src/v3/types.ts",), "build_failure", True,
            language="typescript", harness_id="node", specimen="javascript",
        )
    ]
    config = {"assay_js": {"tasks": ["JS1"], "seeds_per_arm": 1}, "bootstrap_seed": 0}

    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode="assay_js", corpus=corpus,
                                    config=config)

    first = view["matrix"][0]
    assert first["language"] == "typescript"
    assert first["specimen"] == "javascript"
    assert first["harm_label"] == "risky"
    assert view["groups"]["by_language"]["typescript"]["total_planned"] == len(
        run_pair.arms_for("risky")
    )
    assert view["groups"]["by_specimen"]["javascript"]["pending"] == len(
        run_pair.arms_for("risky")
    )


def test_trace_sidecars_are_summarized(tmp_path):
    run_id = "r1"
    run_dir = _write_run(tmp_path, run_id, [_oc("JS1", models.ARM_PEBRA, 0)])
    token = run_pair._arm_token(models.ARM_PEBRA, run_id)
    clone = run_dir / f"JS1_seed0_{token}"
    clone.mkdir(parents=True)
    (clone / "subject_trace.json").write_text(json.dumps({
        "schema_version": "agent_ab.subject_trace.v1",
        "task_id": "JS1",
        "arm": models.ARM_PEBRA,
        "seed": 0,
        "model": "deepseek-v4-flash",
        "final": {
            "timed_out": True,
            "limit_reason": "wall_clock",
            "final_stop_reason": "tool_use",
            "turn_count": 3,
            "duration_seconds": 600.1,
            "protocol_file_read": True,
            "served_models": ["deepseek-v4-flash"],
            "modified_files": ["packages/zod/src/v3/types.ts"],
        },
        "turns": [{"stop_reason": "tool_use", "latency_seconds": 11.2}],
        "tool_calls": [
            {"sequence": 0, "name": "read_file", "latency_seconds": 0.1},
            {"sequence": 1, "name": "advisory_check", "advisory_decision": "revise_safer",
             "latency_seconds": 0.2},
            {"sequence": 2, "name": "write_file", "blocked": True, "latency_seconds": 0.3},
        ],
    }), encoding="utf-8")

    view = aggregate.build_run_view(run_id, ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})

    assert view["traces"] == [{
        "clone": clone.name,
        "task_id": "JS1",
        "seed": 0,
        "arm": models.ARM_PEBRA,
        "model": "deepseek-v4-flash",
        "timed_out": True,
        "limit_reason": "wall_clock",
        "error": None,
        "final_stop_reason": "tool_use",
        "turn_count": 3,
        "duration_seconds": 600.1,
        "protocol_file_read": True,
        "served_models": ["deepseek-v4-flash"],
        "modified_files": ["packages/zod/src/v3/types.ts"],
        "tool_call_count": 3,
        "advisory_count": 1,
        "write_count": 1,
        "blocked_write_count": 1,
        "last_turn_stop_reason": "tool_use",
        "last_turn_latency_seconds": 11.2,
        "last_tool_name": "write_file",
        "last_tool_latency_seconds": 0.3,
        "advisory_decisions": ["revise_safer"],
    }]


def test_assay_scoreboard_surfaces_verdict_and_pair_counts(tmp_path):
    arms = run_pair.arms_for("risky")
    outcomes = [_oc("T1", a, 0, harm=(a == models.ARM_SHAM)) for a in arms]
    _write_run(tmp_path, "r1", outcomes)
    corpus = [TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)]
    config = {"assay": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0}
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode="assay", corpus=corpus, config=config)
    sb = view["scoreboard"]
    assert "verdict" in sb and "arms" in sb and "pairwise" in sb
    # pair counts must be surfaced so the UI can suppress an early-run verdict
    assert any("n_pairs_risky" in p for p in sb["pairwise"])


def test_scoreboard_prefers_final_report_json_when_present(tmp_path):
    outcomes = [_oc("T1", a, 0, harm=(a == models.ARM_SHAM)) for a in run_pair.arms_for("risky")]
    run_dir = _write_run(tmp_path, "r1", outcomes)
    expected = {
        "verdict": "REPORT_IS_AUTHORITATIVE",
        "preflight_status": {"oracle": "skipped", "graph": "skipped", "revise_safer": "skipped"},
        "served_models": ["deepseek-test"],
        "scoring_mode": "custom_report_scope",
        "arms": {},
        "pairwise": [],
    }
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "assay_r1.json").write_text(json.dumps(expected), encoding="utf-8")
    corpus = [TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)]
    config = {"assay": {"tasks": ["T1"], "seeds_per_arm": 1}, "bootstrap_seed": 0}

    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode="assay", corpus=corpus,
                                    config=config)

    assert view["scoreboard"]["verdict"] == "REPORT_IS_AUTHORITATIVE"
    assert view["scoreboard"]["served_models"] == ["deepseek-test"]
    assert view["scoreboard"]["scoring_mode"] == "custom_report_scope"


# --- dashboards + arm attribution (blinded clone dirs) ------------------------------------------


def test_dashboard_stores_are_attributed_to_arms(tmp_path):
    run_id = "r1"
    outcomes = [_oc("T1", models.ARM_PEBRA, 0), _oc("T1", models.ARM_SHAM, 0)]
    run_dir = _write_run(tmp_path, run_id, outcomes)
    # only pebra writes a store; its clone dir is the blinded token
    token = run_pair._arm_token(models.ARM_PEBRA, run_id)
    clone = run_dir / f"T1_seed0_{token}"
    (clone / "repo").mkdir(parents=True)
    (clone / "pebra.db").write_text("", encoding="utf-8")
    view = aggregate.build_run_view(run_id, ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})
    dbs = {d["clone"]: d for d in view["dashboards"]}
    assert dbs[clone.name]["arm"] == models.ARM_PEBRA
    assert dbs[clone.name]["arm_matched"] is True
    cmd = dbs[clone.name]["launch_command"]
    assert "pebra" in cmd and "dashboard" in cmd and "--db" in cmd  # a runnable command string


def test_legacy_treatment_arm_store_is_attributed(tmp_path):
    # The legacy treatment arm ALSO runs the real advisory backend with a pebra.db (pilot is the default
    # mode), so its store must attribute to "treatment", not render as unattributed.
    run_id = "r1"
    run_dir = _write_run(tmp_path, run_id, [_oc("T1", models.ARM_TREATMENT, 0)])
    token = run_pair._arm_token(models.ARM_TREATMENT, run_id)
    clone = run_dir / f"T1_seed0_{token}"
    (clone / "repo").mkdir(parents=True)
    (clone / "pebra.db").write_text("", encoding="utf-8")
    view = aggregate.build_run_view(run_id, ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})
    dbs = {d["clone"]: d for d in view["dashboards"]}
    assert dbs[clone.name]["arm"] == models.ARM_TREATMENT
    assert dbs[clone.name]["arm_matched"] is True


def test_dot_run_id_raises_not_found(tmp_path):
    for bad in (".", ".."):
        with pytest.raises(aggregate.RunNotFound):
            aggregate.build_run_view(bad, ab_out=tmp_path, mode=None, corpus=[],
                                     config={"bootstrap_seed": 0})


def test_unmatched_store_gets_null_arm_not_crash(tmp_path):
    run_id = "r1"
    run_dir = _write_run(tmp_path, run_id, [_oc("T1", models.ARM_PEBRA, 0)])
    clone = run_dir / "T1_seed0_deadbeefcafe"  # not a real arm token
    (clone / "repo").mkdir(parents=True)
    (clone / "pebra.db").write_text("", encoding="utf-8")
    view = aggregate.build_run_view(run_id, ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})
    dbs = {d["clone"]: d for d in view["dashboards"]}
    assert dbs[clone.name]["arm"] is None
    assert dbs[clone.name]["arm_matched"] is False


# --- phase / mode / coverage -------------------------------------------------------------------


def test_run_status_is_authoritative_for_phase_and_mode(tmp_path):
    _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0)],
               run_status={"run_id": "r1", "mode": "assay_js", "phase": "running", "updated_at": "x"})
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode=None, corpus=[],
                                    config={"assay_js": {"tasks": [], "seeds_per_arm": 1},
                                            "bootstrap_seed": 0})
    assert view["phase"] == "running"
    assert view["mode"] == "assay_js"


def test_coverage_panel_reads_preflight_artifact(tmp_path):
    _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0)],
               coverage={"by_language": {"typescript": {"tier": "full", "node_count": 12}}})
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})
    assert view["coverage"]["available"] is True
    assert view["coverage"]["by_language"]["typescript"]["tier"] == "full"


def test_coverage_absent_is_reported_not_crashed(tmp_path):
    _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0)])
    view = aggregate.build_run_view("r1", ab_out=tmp_path, mode=None, corpus=[],
                                    config={"bootstrap_seed": 0})
    assert view["coverage"]["available"] is False


# --- run index + errors ------------------------------------------------------------------------


def test_list_runs_reports_each_run(tmp_path):
    _write_run(tmp_path, "r1", [_oc("T1", models.ARM_CONTROL, 0)])
    _write_run(tmp_path, "r2", [_oc("T1", models.ARM_CONTROL, 0),
                                _oc("T1", models.ARM_TREATMENT, 0)], reports=True)
    runs = {r["run_id"]: r for r in aggregate.list_runs(ab_out=tmp_path)}
    assert set(runs) == {"r1", "r2"}
    assert runs["r2"]["done_count"] == 2


def test_unknown_run_raises(tmp_path):
    with pytest.raises(aggregate.RunNotFound):
        aggregate.build_run_view("nope", ab_out=tmp_path, mode=None, corpus=[],
                                 config={"bootstrap_seed": 0})
