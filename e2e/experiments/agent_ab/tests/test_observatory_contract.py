"""Anti-drift contract: the run observatory reaches into producer internals (some private, _-prefixed)
instead of reimplementing them. Each test below pins ONE reused symbol's shape, so a rename/removal or a
shape change in orchestrator/run_pair/scorecard/render_report/launch_dashboard fails HERE — loudly, at
the exact seam — rather than silently mis-rendering the observatory. Mirrors the parity-test discipline
used for the boundary lookup helpers.
"""

from __future__ import annotations

import dataclasses
import sys

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import launch_dashboard, orchestrator, run_pair


def _oc(arm, *, harm=False, harm_label="risky"):
    return models.RunOutcome(
        task_id="T1", arm=arm, seed=0, harm_label=harm_label, harm_materialized=harm,
        task_completed=True, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=harm, test_failed=False, edit_cycle_count=1, advisory_called=False,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def test_outcome_from_dict_roundtrips():
    oc = _oc(models.ARM_PEBRA)
    back = orchestrator._outcome_from_dict(dataclasses.asdict(oc))
    assert back.arm == models.ARM_PEBRA and back.task_id == "T1"


def test_plan_expands_task_by_seed():
    corpus = [TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)]
    plan = orchestrator._plan(corpus, ["T1"], 2)
    assert [(s.task_id, seed) for s, seed in plan] == [("T1", 0), ("T1", 1)]


def test_arms_for_membership():
    risky = run_pair.arms_for("risky")
    assert models.ARM_SHAM in risky and models.ARM_PEBRA in risky
    assert models.ARM_ORACLE_POSITIVE not in run_pair.arms_for("safe")


def test_store_writing_arms_are_the_real_advisory_set():
    # aggregate._STORE_ARMS derives from this set to attribute pebra.db stores. If the runner's
    # real-advisory membership changes, dashboard attribution must follow — pin it here.
    assert run_pair._REAL_ADVISORY_ARMS >= {models.ARM_TREATMENT, models.ARM_PEBRA,
                                            models.ARM_PEBRA_GRAPH_REPAIR}


def test_arm_token_is_deterministic_12_hex():
    tok = run_pair._arm_token(models.ARM_PEBRA, "r1")
    assert tok == run_pair._arm_token(models.ARM_PEBRA, "r1")
    assert len(tok) == 12 and all(c in "0123456789abcdef" for c in tok)


def test_launch_dashboard_surface():
    assert launch_dashboard.list_run_dbs("nope", ab_out=__import__("pathlib").Path(".")) == []
    cmd = launch_dashboard.dashboard_command("/r", "/r/db", 4500)
    assert cmd[:4] == [sys.executable, "-m", "pebra", "dashboard"]
    assert isinstance(launch_dashboard.render_command(cmd), str)
    assert launch_dashboard._RUN_ID_RE.fullmatch("r1") and not launch_dashboard._RUN_ID_RE.fullmatch("../x")


def test_aggregate_ab_shape():
    ab = scorecard.aggregate([_oc(models.ARM_CONTROL, harm=True), _oc(models.ARM_TREATMENT)])
    assert hasattr(ab, "net_benefit") and hasattr(ab, "n_pairs_risky")
    j = render_report.to_json(ab)
    assert "net_benefit" in j["endpoints"] and "n_pairs" in j


def test_aggregate_assay_shape():
    arms = run_pair.arms_for("risky")
    outcomes = [_oc(a, harm=(a == models.ARM_SHAM)) for a in arms]
    assay = scorecard.aggregate_assay(outcomes, arms=list(arms))
    assert isinstance(assay.arm_metrics, dict) and hasattr(assay.interpretation, "verdict")
    j = render_report.assay_to_json(assay)
    assert "verdict" in j and "arms" in j and "pairwise" in j
    assert all("n_pairs_risky" in p for p in j["pairwise"])
