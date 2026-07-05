"""Assay wiring: multi-arm report render + the N-arm resume/completion logic."""

from __future__ import annotations

import json
from types import SimpleNamespace

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import orchestrator

_ARMS = [
    models.ARM_SHAM,
    models.ARM_ORACLE_POSITIVE,
    models.ARM_ENFORCED_CONTROL,
    models.ARM_BLAST_RADIUS,
    models.ARM_PEBRA,
]


def _o(task, arm, seed, harm_label, harm):
    return models.RunOutcome(
        task_id=task, arm=arm, seed=seed, harm_label=harm_label, harm_materialized=harm,
        task_completed=not harm, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=harm, test_failed=False, edit_cycle_count=1, advisory_called=True,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_NO_RESTRICTION,
        blinding_leak=False, blinding_terms=(), timed_out=False)


def _assay_metrics():
    outs = []
    for arm in _ARMS:
        outs.append(_o("T1", arm, 0, "risky", harm=(arm == models.ARM_SHAM)))  # only sham harms
    return scorecard.aggregate_assay(outs, arms=_ARMS)


def test_render_assay_markdown_shows_verdict_arms_pairwise():
    m = _assay_metrics()
    md = render_report.render_assay_markdown(m, run_id="r1")
    assert f"VERDICT: {m.interpretation.verdict}" in md
    for arm in _ARMS:
        assert arm in md  # every arm in the per-arm table
    assert "harm_avoided" in md and "net_benefit" in md  # pairwise table present


def test_assay_to_json_has_verdict_gate_trace_and_pairwise():
    m = _assay_metrics()
    js = render_report.assay_to_json(m)
    assert js["verdict"] == m.interpretation.verdict
    assert set(js["arms"]) == set(_ARMS)
    assert set(js["gate_trace"]) == {"task_has_headroom", "assay_detects_realistic",
                                     "pebra_has_efficacy", "pebra_exceeds_blast"}
    assert any(p["intervention"] == models.ARM_PEBRA and p["baseline"] == models.ARM_SHAM
               for p in js["pairwise"])


def test_assay_report_invalidates_skipped_preflight():
    m = _assay_metrics()
    preflight = {"oracle": "skipped", "graph": "passed"}
    js = render_report.assay_to_json(m, preflight_status=preflight)
    md = render_report.render_assay_markdown(m, run_id="r1", preflight_status=preflight)

    assert js["verdict"] == "INVALID_DEBUG_RUN"
    assert js["raw_verdict"] == m.interpretation.verdict
    assert js["claim_valid"] is False
    assert "INVALID DEBUG RUN" in js["conclusion"]
    assert "## VERDICT: INVALID_DEBUG_RUN" in md
    assert f"Raw assay verdict: {m.interpretation.verdict}" in md


def test_assay_pairwise_reports_safe_pair_count():
    js = render_report.assay_to_json(_assay_metrics())
    assert all("n_pairs_safe" in p for p in js["pairwise"])
    md = render_report.render_assay_markdown(_assay_metrics(), run_id="r1")
    assert "safe_pairs" in md


def test_write_assay_report_writes_both_files(tmp_path):
    md_path, json_path = render_report.write_assay_report(_assay_metrics(), out_dir=tmp_path, run_id="r1")
    assert md_path.is_file() and json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["n_arms"] == 5


def test_completed_units_risky_needs_all_five_arms():
    specs = {"T1": SimpleNamespace(task_id="T1", harm_label="risky")}
    partial = [_o("T1", a, 0, "risky", False)
               for a in (models.ARM_SHAM, models.ARM_ORACLE_POSITIVE, models.ARM_BLAST_RADIUS,
                         models.ARM_PEBRA)]  # missing enforced_control
    assert orchestrator._completed_units(partial, specs) == set()
    full = partial + [_o("T1", models.ARM_ENFORCED_CONTROL, 0, "risky", False)]
    assert ("T1", 0) in orchestrator._completed_units(full, specs)


def test_completed_units_safe_needs_three_arms():
    specs = {"B1": SimpleNamespace(task_id="B1", harm_label="safe")}
    three = [_o("B1", a, 0, "safe", False)
             for a in (models.ARM_SHAM, models.ARM_BLAST_RADIUS, models.ARM_PEBRA)]
    assert ("B1", 0) in orchestrator._completed_units(three, specs)
    assert orchestrator._completed_units(three[:2], specs) == set()  # missing an arm -> not complete
