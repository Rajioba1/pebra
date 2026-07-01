"""Scenario C — outcome-to-graph attribution on a REAL C# repo (Phase 1, PROVENANCE ONLY).

When the scoped IWorkspace.CanCloseAsync signature edit breaks the build, PEBRA resolves the compiler's
diagnostics to CodeGraph nodes/edges — the primary proof being the class/interface ``implements`` edge
(WorkspaceViewModel --implements--> IWorkspace) — and records a ``graph_attribution`` blob as outcome
provenance, plus a LEARNED ``event_outcomes={"public_api_break": true}`` label. Graph-backed MODIFY
also predicts ``dependency_break`` on this request, but the real compiler outcome records only the
public API contract break. That one recorded event joins to a real calibration target instead of being
an orphan detail key — the +1 observed risk row below is that join.

GOVERNING CLAIM (locked): Phase 1 proves outcome-to-graph attribution PLUMBING on real diagnostics; it
does NOT claim graph-calibrated learning. Attribution is evidence — it never moves a score (asserted by
``assess_has_attribution_key`` being False). The delta-only invariant's real proof is the UNIT test
``test_delta_excludes_baseline_diagnostic``; the E2E check below only proves this edit yields
contract-break codes.
"""

from __future__ import annotations

from pathlib import Path

from e2e.utils import report_generator as rg


def test_delta_diagnostics_are_non_empty(compiler_attribution_state):
    s = compiler_attribution_state
    assert s.baseline_build_passed is True  # clean tree builds; baseline diagnostic set is empty
    assert s.delta_diagnostic_count > 0  # the edit produced NEW compiler diagnostics


def test_delta_contains_only_contract_break_codes(compiler_attribution_state):
    # NOT a delta-mechanism proof (baseline was empty) — an honest check that the edit's new diagnostics
    # are the expected interface-contract breaks (CS0535 implementers / CS7036 callers), not stray noise.
    s = compiler_attribution_state
    assert set(s.delta_codes) <= {"CS0535", "CS7036"}, s.delta_codes


def test_graph_attribution_blob_has_required_shape(compiler_attribution_state):
    s = compiler_attribution_state
    assert s.graph_attribution is not None
    for key in (
        "error_kind", "diagnostic", "broken_file", "broken_symbol", "interface", "edited_symbol",
        "edge_kind", "implements_edge", "method_match", "predicted_callers", "actual_broken_files",
        "attribution_method", "attribution_confidence", "unresolved_count", "graph_freshness",
    ):
        assert key in s.graph_attribution, f"missing key {key!r}"


def test_diagnostic_resolves_to_the_implements_edge(compiler_attribution_state):
    # The probe confirmed WorkspaceViewModel --implements--> IWorkspace exists in this repo's index, so
    # the primary class/interface-level proof must resolve — not fall back to unresolved.
    s = compiler_attribution_state
    assert s.attribution_method != "unresolved"
    assert s.implements_edge is True
    assert s.attribution_confidence >= 0.9
    assert s.graph_attribution["interface"] == "IWorkspace"


def test_predicted_callers_and_broken_files_are_separate_integers(compiler_attribution_state):
    # Honest separation: predicted callers (fan-in) and materialized broken files are distinct numbers,
    # NOT a subset relationship.
    s = compiler_attribution_state
    assert isinstance(s.predicted_callers, int)
    assert isinstance(s.actual_broken_files, int)
    assert s.actual_broken_files >= 1  # at least one implementer/caller broke


def test_event_outcomes_records_the_predicted_event(compiler_attribution_state):
    # The recorded event is public_api_break — the event the request predicts — NOT an orphan key.
    s = compiler_attribution_state
    assert s.event_outcomes_recorded == {"public_api_break": True}


def test_recorded_event_is_a_learned_target_not_an_orphan(compiler_attribution_state):
    # 100 cycles each observe p_success (1 row/cycle); only the real cycle also records
    # p_event.public_api_break as an outcome. Dependency_break may be predicted by the MODIFY graph
    # model, but it is intentionally uncounted here because no dependency_break outcome was recorded.
    # So 101 = 100 p_success rows + 1 observed public_api_break row that joined a real calibration
    # target (prediction_error._risk_actual) rather than being dropped as an unpredicted detail key.
    s = compiler_attribution_state
    assert s.observed_risk_rows == 101


def test_attribution_is_load_bearing_learning_not_decorative(compiler_attribution_state):
    # Load-bearing: 99 seeds don't promote; the real cycle's 100th p_success row tips the gate.
    s = compiler_attribution_state
    assert s.promoted_pre is False
    assert s.promotion["risk"]["promoted"] is True, s.promotion["risk"]["veto_reasons"]


def test_attribution_never_appears_in_the_assess_payload(compiler_attribution_state):
    # GOVERNANCE: attribution is provenance recorded at outcome time. It must never leak into the scored
    # assess payload — so no score/decision can depend on it.
    s = compiler_attribution_state
    assert s.assess_has_attribution_key is False


def test_unresolved_count_is_non_negative_integer(compiler_attribution_state):
    s = compiler_attribution_state
    assert isinstance(s.unresolved_count, int) and s.unresolved_count >= 0


def test_report_surfaces_attribution_fields(compiler_attribution_state):
    s = compiler_attribution_state
    report = rg.write_report(
        [rg.FeatureResult("external_compiler_attribution", "PASS", "codegraph",
                          graph_evidence={"attribution": s.graph_attribution})],
        out_dir=Path("e2e/out/reports"), run_id="external_compiler_attribution",
    )
    md = report.read_text(encoding="utf-8")
    assert "Attribution method" in md
    assert "Predicted callers (pre-edit fan-in)" in md
    assert "Materialized breakage" in md
    assert report.exists()
