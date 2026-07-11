"""Graph-wide MODIFY risk model.

High-fan-in modifications can break code outside the edited file. The model should translate trusted
graph structure into ordinary risk events so the existing expected-loss gates decide; unresolved graph
data must not be treated as "low risk".
"""

from __future__ import annotations

import pytest

from pebra.core import modify_risk_model as mrm
from pebra.core.models import (
    ArchitectureEvidence,
    CandidateAggregateEvidence,
    FanInEvidence,
    SymbolDiffEvidence,
)


def _sde(**over):
    return SymbolDiffEvidence(
        parsed_patch_available=True,
        changed_symbols=["src/core.py::hot_path"],
        max_change_kind=over.pop("max_change_kind", "CONTRACT"),
        visibility=over.pop("visibility", "internal"),
        consequential_symbol_changed=over.pop("consequential_symbol_changed", True),
        symbol_fan_in_percentile=over.pop("symbol_fan_in_percentile", 0.95),
        **over,
    )


def _fanin(**over):
    return FanInEvidence(
        symbol_fan_in_percentile=over.pop("symbol_fan_in_percentile", 0.95),
        symbol_caller_count=over.pop("symbol_caller_count", 13),
        resolution_method=over.pop("resolution_method", "location"),
        graph_freshness=over.pop("graph_freshness", "fresh"),
        **over,
    )


def _events(sde=None, fanin=None, **kw):
    return mrm.events_for_modify_risk(
        symbol_diff=sde or _sde(),
        fanin=fanin if fanin is not None else _fanin(),
        arch=kw.pop("arch", ArchitectureEvidence()),
        criticality_stage=kw.pop("criticality_stage", "C3"),
        is_schema_change=kw.pop("is_schema_change", False),
        is_migration=kw.pop("is_migration", False),
        candidate_aggregate=kw.pop("candidate_aggregate", CandidateAggregateEvidence()),
    )


def _by(events, name):
    return next((e for e in events if e["event"] == name), None)


def test_high_fanin_internal_contract_modify_injects_dependency_break():
    events = _events()
    dep = _by(events, "dependency_break")

    assert dep is not None
    assert dep["p_event"] > 0.20
    assert dep["elicited_disutility"] == pytest.approx(0.60)
    assert dep["probability_source_type"] == "prior_uncalibrated"


def test_coarse_structural_tier_counts_as_unknown_change_for_large_owner():
    # Monotonic-safety: a coarse codegraph_structural classification of a BEHAVIORAL internal owner in
    # a LARGE span must still inject dependency_break (it counts as unknown_change), so reclassifying
    # UNKNOWN->BEHAVIORAL for a non-Python owner never silently drops the MODIFY event.
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", visibility="internal",
             consequential_symbol_changed=False, symbol_fan_in_percentile=0.10,
             structure_tier="codegraph_structural"),
        _fanin(symbol_fan_in_percentile=0.10, symbol_caller_count=1, max_owner_span_lines=400),
    )
    assert _by(events, "dependency_break") is not None


def test_python_ast_behavioral_internal_no_event_baseline():
    # Contrast/baseline: the SAME low-fan-in internal BEHAVIORAL change WITHOUT the coarse tier does
    # not inject dependency_break — proving the event above is due to the coarse-tier uncertainty term.
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", visibility="internal",
             consequential_symbol_changed=False, symbol_fan_in_percentile=0.10),
        _fanin(symbol_fan_in_percentile=0.10, symbol_caller_count=1, max_owner_span_lines=400),
    )
    assert _by(events, "dependency_break") is None


def test_codegraph_semantic_tier_counts_as_unknown_change_like_coarse():
    # Monotonic safety: the semantic tier proves the SIGNATURE is unchanged, which does NOT prove the
    # BODY/behavior is unchanged (the body_changed floor is always set) — so a large-owner semantic
    # BEHAVIORAL change must still inject dependency_break, exactly like the coarse tier. Treating it as
    # "certain / no event" would let it drop escalation below the coarse floor (the audited masking bug).
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", visibility="internal",
             consequential_symbol_changed=False, symbol_fan_in_percentile=0.10,
             structure_tier="codegraph_semantic"),
        _fanin(symbol_fan_in_percentile=0.10, symbol_caller_count=1, max_owner_span_lines=400),
    )
    assert _by(events, "dependency_break") is not None


def test_public_contract_modify_also_injects_public_api_break():
    events = _events(_sde(visibility="public_api"))

    assert _by(events, "public_api_break") is not None
    assert _by(events, "dependency_break") is None


def test_public_consequential_modify_injects_public_api_break_even_when_direct_fanin_is_low():
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", visibility="public", consequential_symbol_changed=True,
             symbol_fan_in_percentile=0.49),
        _fanin(symbol_fan_in_percentile=0.49, symbol_caller_count=0),
    )

    assert _by(events, "public_api_break") is not None
    assert _by(events, "dependency_break") is None


def test_untrusted_graph_does_not_fabricate_modify_risk():
    events = _events(fanin=_fanin(resolution_method="unresolved", graph_freshness="unknown"))

    assert events == []


def test_absent_graph_does_not_fabricate_modify_risk():
    events = mrm.events_for_modify_risk(
        symbol_diff=_sde(visibility="public", max_change_kind="CONTRACT"),
        fanin=None,
        arch=ArchitectureEvidence(),
        criticality_stage="C3",
    )

    assert events == []


def test_stale_graph_does_not_fabricate_modify_risk():
    events = _events(
        fanin=_fanin(
            graph_freshness="stale",
            owner_kinds=("interface",),
            max_owner_span_lines=180,
            outgoing_edge_counts={"implements": 13},
        )
    )

    assert events == []


def test_ambiguous_name_fallback_does_not_fabricate_modify_risk():
    events = _events(
        fanin=_fanin(
            resolution_method="name_fallback_ambiguous",
            owner_kinds=("interface",),
            max_owner_span_lines=180,
            outgoing_edge_counts={"implements": 13},
        )
    )

    assert events == []


def test_low_fanin_internal_contract_modify_is_not_escalated():
    events = _events(
        _sde(symbol_fan_in_percentile=0.20, consequential_symbol_changed=False),
        _fanin(symbol_fan_in_percentile=0.20, symbol_caller_count=1),
    )

    assert events == []


def test_large_owner_contract_modify_injects_dependency_break_even_when_direct_fanin_is_low():
    events = _events(
        _sde(max_change_kind="CONTRACT", symbol_fan_in_percentile=0.25,
             consequential_symbol_changed=True),
        _fanin(symbol_fan_in_percentile=0.25, symbol_caller_count=1,
               owner_kinds=("method",), max_owner_span_lines=90,
               resolved_symbol_count=1, outgoing_edge_counts={"calls": 8}),
    )

    dep = _by(events, "dependency_break")
    assert dep is not None
    expected = (
        mrm._BASELINE_CONTRACT
        + mrm._LARGE_OWNER_BONUS
        + mrm._OUTGOING_EDGE_BONUS_MAX
        + mrm._C3_C4_BONUS["C3"]
        + 0.25 * (mrm._FANIN_BONUS_MAX / mrm._HIGH_FANIN_THRESHOLD)
    )
    assert dep["p_event"] == pytest.approx(expected)


def test_implements_extends_impact_escalates_contract_modify_when_direct_fanin_is_low():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=0.20,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=0.20,
            symbol_caller_count=1,
            modify_impact_count=13,
            modify_impact_percentile=0.95,
            modify_impact_edge_counts={"implements": 9, "extends": 4},
        ),
    )

    dep = _by(events, "dependency_break")
    expected = (
        mrm._BASELINE_CONTRACT
        + mrm._C3_C4_BONUS["C3"]
        + mrm._FANIN_BONUS_MAX
    )
    assert dep is not None
    assert dep["p_event"] == pytest.approx(expected)


def test_transitive_modify_impact_escalates_contract_modify_when_direct_impact_is_low():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=0.10,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=0.10,
            symbol_caller_count=1,
            modify_impact_count=1,
            modify_impact_percentile=0.20,
            modify_transitive_impact_count=9,
            modify_transitive_impact_percentile=0.95,
            modify_transitive_depth_buckets={1: 1, 2: 5, 3: 3},
            modify_repo_blast_fraction=0.12,
        ),
    )

    dep = _by(events, "dependency_break")
    assert len(events) == 1
    assert dep is not None
    assert dep["p_event"] == pytest.approx(
        mrm._BASELINE_CONTRACT + mrm._C3_C4_BONUS["C3"] + mrm._FANIN_BONUS_MAX
    )


def test_zero_count_transitive_percentile_does_not_fabricate_high_modify_impact():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=0.0,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=0.0,
            symbol_caller_count=0,
            modify_impact_count=0,
            modify_impact_percentile=0.0,
            modify_transitive_impact_count=0,
            modify_transitive_impact_percentile=0.95,
        ),
    )

    assert events == []


def test_pure_implementer_impact_escalates_contract_modify_with_zero_direct_callers():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=0.0,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=1.0,
            symbol_caller_count=0,
            modify_impact_count=5,
            modify_impact_percentile=0.95,
            modify_impact_edge_counts={"implements": 5},
        ),
    )

    dep = _by(events, "dependency_break")
    assert dep is not None
    assert dep["p_event"] == pytest.approx(
        mrm._BASELINE_CONTRACT + mrm._C3_C4_BONUS["C3"] + mrm._FANIN_BONUS_MAX
    )


def test_zero_direct_callers_ignore_symbol_percentile_when_structural_impact_is_below_cap():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=1.0,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=1.0,
            symbol_caller_count=0,
            max_owner_span_lines=90,
            modify_impact_count=4,
            modify_impact_percentile=0.30,
        ),
    )

    dep = _by(events, "dependency_break")
    assert dep is not None
    expected = (
        mrm._BASELINE_CONTRACT
        + mrm._LARGE_OWNER_BONUS
        + mrm._C3_C4_BONUS["C3"]
        + 0.30 * (mrm._FANIN_BONUS_MAX / mrm._HIGH_FANIN_THRESHOLD)
    )
    assert dep["p_event"] == pytest.approx(expected)


def test_graph_contract_surface_adds_public_api_break_even_when_request_visibility_is_internal():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            visibility="internal",
            symbol_fan_in_percentile=0.20,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=0.20,
            symbol_caller_count=1,
            modify_impact_count=13,
            modify_impact_percentile=0.95,
            contract_surface_kind="interface_method",
            is_exported_contract=True,
            is_abstract_or_interface_contract=True,
            has_signature_metadata=True,
        ),
    )

    assert _by(events, "public_api_break") is not None
    assert _by(events, "dependency_break") is None


def test_graph_contract_metadata_alone_does_not_escalate_plain_low_impact_body_edit():
    events = _events(
        _sde(
            max_change_kind="BEHAVIORAL",
            visibility="internal",
            symbol_fan_in_percentile=0.20,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=0.20,
            symbol_caller_count=1,
            modify_impact_count=1,
            modify_impact_percentile=0.20,
            contract_surface_kind="interface_method",
            is_exported_contract=True,
            is_abstract_or_interface_contract=True,
            has_signature_metadata=True,
        ),
    )

    assert events == []


def test_large_owner_plain_behavioral_modify_without_consequence_is_not_escalated():
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", symbol_fan_in_percentile=0.25,
             consequential_symbol_changed=False),
        _fanin(symbol_fan_in_percentile=0.25, symbol_caller_count=1,
               owner_kinds=("method",), max_owner_span_lines=90,
               outgoing_edge_counts={"calls": 8}),
    )

    assert events == []


def test_ordinary_behavioral_modify_without_high_fanin_is_not_escalated():
    events = _events(
        _sde(max_change_kind="BEHAVIORAL", symbol_fan_in_percentile=0.20,
             consequential_symbol_changed=False),
        _fanin(symbol_fan_in_percentile=0.20, symbol_caller_count=1),
    )

    assert events == []


def test_schema_modify_gets_domain_event_when_graph_trusted():
    events = _events(is_schema_change=True)

    assert _by(events, "api_contract_break") is not None
    assert _by(events, "dependency_break") is None


def test_modify_probability_is_capped():
    events = _events(_sde(visibility="public_api"), _fanin(symbol_fan_in_percentile=1.0),
                     arch=ArchitectureEvidence(domain_entrypoint=True), is_schema_change=True)

    assert _by(events, "api_contract_break")["p_event"] <= mrm._P_EVENT_CAP


def test_multi_owner_breadth_increases_probability_above_worst_owner_floor():
    baseline = _by(_events(), "dependency_break")["p_event"]
    aggregate = CandidateAggregateEvidence(
        file_count=2,
        owner_count=2,
        max_owner_exposure=0.95,
        cumulative_exposure=0.95,
        breadth_bonus=0.04,
    )

    cumulative = _by(_events(candidate_aggregate=aggregate), "dependency_break")["p_event"]

    assert cumulative > baseline
    assert cumulative <= mrm._P_EVENT_CAP


def test_single_owner_candidate_aggregate_preserves_existing_probability():
    baseline = _events()
    aggregate = CandidateAggregateEvidence(
        file_count=1,
        owner_count=1,
        max_owner_exposure=0.95,
        cumulative_exposure=0.95,
        breadth_bonus=0.0,
    )

    assert _events(candidate_aggregate=aggregate) == baseline


def test_public_unknown_low_graph_context_is_not_escalated():
    events = _events(
        _sde(
            max_change_kind="UNKNOWN",
            visibility="public",
            consequential_symbol_changed=False,
            symbol_fan_in_percentile=0.50,
        ),
        _fanin(symbol_fan_in_percentile=0.50, symbol_caller_count=2),
    )

    assert events == []


def test_zero_count_percentiles_do_not_fabricate_high_modify_impact():
    events = _events(
        _sde(
            max_change_kind="CONTRACT",
            symbol_fan_in_percentile=1.0,
            consequential_symbol_changed=False,
        ),
        _fanin(
            symbol_fan_in_percentile=1.0,
            symbol_caller_count=0,
            modify_impact_count=0,
            modify_impact_percentile=1.0,
        ),
    )

    assert events == []


def test_repo_blast_fraction_does_not_change_modify_risk_math():
    base_events = _events(
        _sde(max_change_kind="CONTRACT", symbol_fan_in_percentile=0.30),
        _fanin(symbol_fan_in_percentile=0.30, symbol_caller_count=2, max_owner_span_lines=90),
    )
    with_fraction = _events(
        _sde(max_change_kind="CONTRACT", symbol_fan_in_percentile=0.30),
        _fanin(
            symbol_fan_in_percentile=0.30,
            symbol_caller_count=2,
            max_owner_span_lines=90,
            modify_repo_blast_fraction=0.95,
        ),
    )

    assert len(base_events) == 1
    assert with_fraction == base_events


def test_public_unknown_high_fanin_modify_is_still_escalated():
    events = _events(
        _sde(max_change_kind="UNKNOWN", visibility="public", consequential_symbol_changed=False),
        _fanin(symbol_fan_in_percentile=0.95, symbol_caller_count=13),
    )

    assert _by(events, "public_api_break") is not None
    assert _by(events, "dependency_break") is None


def test_delete_file_operation_is_excluded_from_modify_risk_even_with_strong_graph():
    events = _events(
        _sde(file_operation_kind="DELETE", visibility="public", max_change_kind="CONTRACT"),
        _fanin(owner_kinds=("interface",), max_owner_span_lines=180,
               outgoing_edge_counts={"implements": 13}),
    )

    assert events == []
