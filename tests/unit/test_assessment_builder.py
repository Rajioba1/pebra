"""Architecture §5/§7, AD-4 — assessment_builder: pure factory AssessmentInput -> scored Assessment.

It receives gathered evidence (never calls a port), composes the pure score modules, sets
action_status=pending (AD-4), and reproduces the spec §10 worked-example score set.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pebra.core import assessment_builder as ab
from pebra.core import models as m
from pebra.core.constants import ActionStatus


def _worked_example_input() -> m.AssessmentInput:
    req = m.AssessmentRequest.single_action(
        task="Fix failing login validation",
        action_id="a1",
        label="Patch validate_login only",
        action_type="edit",
        affected_symbols=["src/auth.py::validate_login"],
    )
    return m.AssessmentInput(
        request=req,
        action=req.candidate_actions[0],
        events=[
            {"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40},
            {"event": "public_api_break", "p_event": 0.03, "elicited_disutility": 0.80},
            {"event": "security_sensitive_change", "p_event": 0.04, "elicited_disutility": 0.90},
        ],
        p_success=0.74,
        immediate_benefit=0.82,
        review_cost=0.12,
        criticality_stage="C3",
        criticality_value=0.80,
        edit_confidence_factors={
            "p_success": 0.74,
            "evidence_quality": 0.78,
            "testability": 0.80,
            "reversibility": 0.92,
            "source_reliability": 0.86,
            "scope_control": 0.92,
        },
        thresholds={
            "max_expected_loss_without_human": 0.45,
            "c3_max_expected_loss_without_human": 0.20,
        },
        variance_breakdown={
            "p_success": 0.0016,
            "benefit": 0.0004,
            "event_losses": 0.0009,
            "review_cost": 0.0004,
            "scenario_variance": 0.0003,
        },
        benefit_delta_evidence=m.BenefitDeltaEvidence(source_type="projected"),
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/auth.py::validate_login"],
            max_change_kind="BEHAVIORAL",
            visibility="internal",
            symbol_fan_in_percentile=0.42,
            consequential_symbol_changed=False,
        ),
        repo_id="repo_local_example",
        repo_root="/abs/path/to/example-repo",
    )


def test_builder_reproduces_worked_example_scores() -> None:
    a = ab.build_assessment(_worked_example_input())
    s = a.scores
    assert s["expected_loss"] == pytest.approx(0.10)
    assert s["benefit"] == pytest.approx(0.82)
    assert s["expected_utility"] == pytest.approx(0.3868)
    assert s["utility_sd"] == pytest.approx(0.06)
    assert s["rau"] == pytest.approx(0.31)
    assert s["edit_confidence"] == pytest.approx(0.8338, abs=1e-4)
    assert s["effective_threshold"] == pytest.approx(0.20)
    assert s["risk_budget_used"] == pytest.approx(0.50)


def test_builder_sets_action_status_pending() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.action_status is ActionStatus.PENDING


def test_builder_applies_final_benefit_override() -> None:
    inp = replace(_worked_example_input(), benefit_override=0.33)
    a = ab.build_assessment(inp)
    assert a.scores["benefit"] == pytest.approx(0.33)
    assert a.scores["benefit_breakdown"].benefit == pytest.approx(0.33)


def test_builder_surfaces_file_operation_axis_in_symbol_scope_audit() -> None:
    inp = _worked_example_input()
    inp = replace(
        inp,
        symbol_diff_evidence=replace(
            inp.symbol_diff_evidence,
            file_operation_kind="DELETE",
            file_operation_paths=("src/auth.py",),
        ),
    )

    sse = ab.build_assessment(inp).scores["symbol_scope_evidence"]

    assert sse["file_operation_kind"] == "DELETE"
    assert sse["file_operation_paths"] == ["src/auth.py"]


def test_builder_surfaces_file_fanin_rollup_for_human_graph_proof() -> None:
    inp = replace(
        _worked_example_input(),
        symbol_diff_evidence=replace(
            _worked_example_input().symbol_diff_evidence,
            file_operation_kind="DELETE",
            file_operation_paths=("src/auth.py",),
        ),
        file_fanin_rollup=m.FileFanInRollup(
            max_caller_count=7,
            distinct_caller_count=13,
            symbol_count=5,
            file_symbol_fanin_rollup_percentile=1.0,
            resolution_method="file_location",
            graph_freshness="fresh",
        ),
    )

    sse = ab.build_assessment(inp).scores["symbol_scope_evidence"]

    assert sse["file_fanin_rollup"]["percentile"] == pytest.approx(1.0)
    assert sse["file_fanin_rollup"]["distinct_caller_count"] == 13
    assert sse["file_fanin_rollup"]["max_caller_count"] == 7
    assert sse["file_fanin_rollup"]["symbol_count"] == 5
    assert sse["file_fanin_rollup"]["resolution_method"] == "file_location"
    assert sse["file_fanin_rollup"]["graph_freshness"] == "fresh"


def test_builder_surfaces_symbol_fanin_graph_provenance() -> None:
    inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            symbol_fan_in_percentile=0.95,
            symbol_caller_count=12,
            resolution_method="location",
            graph_freshness="fresh",
            provider_version="1.1.1",
            index_version="24",
            fallback_reason=None,
            owner_kinds=("method", "interface", "class"),
            max_owner_span_lines=91,
            resolved_symbol_count=3,
            incoming_edge_counts={"calls": 12},
            outgoing_edge_counts={"implements": 4, "references": 2},
            modify_impact_count=14,
            modify_impact_percentile=0.97,
            modify_impact_edge_counts={"calls": 12, "implements": 2},
            modify_transitive_impact_count=21,
            modify_transitive_impact_percentile=0.99,
            modify_transitive_depth_buckets={1: 14, 2: 5, 3: 2},
            modify_repo_blast_fraction=0.08,
            modify_repo_graph_node_count=260,
            container_hierarchy_kinds=("class", "namespace"),
            graph_file_size_bytes=240_000,
            graph_file_node_count=750,
            graph_file_error_count=1,
            contract_surface_kind="interface_method",
            is_exported_contract=True,
            is_abstract_or_interface_contract=True,
            has_signature_metadata=True,
        ),
    )

    sse = ab.build_assessment(inp).scores["symbol_scope_evidence"]

    assert sse["symbol_fanin"]["percentile"] == pytest.approx(0.95)
    assert sse["symbol_fanin"]["caller_count"] == 12
    assert sse["symbol_fanin"]["resolution_method"] == "location"
    assert sse["symbol_fanin"]["graph_freshness"] == "fresh"
    assert sse["symbol_fanin"]["owner_kinds"] == ["class", "interface", "method"]
    assert sse["symbol_fanin"]["max_owner_span_lines"] == 91
    assert sse["symbol_fanin"]["resolved_symbol_count"] == 3
    assert sse["symbol_fanin"]["incoming_edge_counts"] == {"calls": 12}
    assert sse["symbol_fanin"]["outgoing_edge_counts"] == {"implements": 4, "references": 2}
    assert sse["symbol_fanin"]["modify_impact_count"] == 14
    assert sse["symbol_fanin"]["modify_impact_percentile"] == pytest.approx(0.97)
    assert sse["symbol_fanin"]["modify_impact_edge_counts"] == {"calls": 12, "implements": 2}
    assert sse["symbol_fanin"]["modify_transitive_impact_count"] == 21
    assert sse["symbol_fanin"]["modify_transitive_impact_percentile"] == pytest.approx(0.99)
    assert sse["symbol_fanin"]["modify_transitive_depth_buckets"] == {1: 14, 2: 5, 3: 2}
    assert sse["symbol_fanin"]["modify_repo_blast_fraction"] == pytest.approx(0.08)
    assert sse["symbol_fanin"]["modify_repo_graph_node_count"] == 260
    assert sse["symbol_fanin"]["container_hierarchy_kinds"] == ["class", "namespace"]
    assert sse["symbol_fanin"]["graph_file_size_bytes"] == 240_000
    assert sse["symbol_fanin"]["graph_file_node_count"] == 750
    assert sse["symbol_fanin"]["graph_file_error_count"] == 1
    assert sse["symbol_fanin"]["contract_surface_kind"] == "interface_method"
    assert sse["symbol_fanin"]["is_exported_contract"] is True
    assert sse["symbol_fanin"]["is_abstract_or_interface_contract"] is True
    assert sse["symbol_fanin"]["has_signature_metadata"] is True
    assert "provider_version" not in sse["symbol_fanin"]
    assert "index_version" not in sse["symbol_fanin"]


def test_builder_uses_tighter_c3_threshold_as_effective() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.scores["effective_threshold"] == pytest.approx(0.20)
    assert a.scores["budget_threshold_key"] == "c3_max_expected_loss_without_human"


def test_builder_confidence_band_high() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.confidence_band == "high"


def test_builder_applies_architecture_centrality_to_scope_control() -> None:
    from dataclasses import replace

    inp = replace(
        _worked_example_input(),
        architecture_evidence=m.ArchitectureEvidence(
            god_node_score=0.95,
            cycle_participation=True,
            bridge_centrality=0.8,
            domain_entrypoint=True,
        ),
    )
    a = ab.build_assessment(inp)
    assert a.scores["edit_confidence_factors"]["scope_control"] == pytest.approx(0.77)
    assert a.scores["edit_confidence"] < ab.build_assessment(_worked_example_input()).scores[
        "edit_confidence"
    ]


def test_builder_applies_codegraph_file_metadata_to_confidence_not_loss() -> None:
    inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            resolution_method="location",
            graph_freshness="fresh",
            graph_file_error_count=1,
            graph_file_size_bytes=240_000,
            graph_file_node_count=750,
        ),
    )

    a = ab.build_assessment(inp)

    assert a.scores["expected_loss"] == pytest.approx(
        ab.build_assessment(_worked_example_input()).scores["expected_loss"]
    )
    assert a.scores["edit_confidence_factors"]["evidence_quality"] == pytest.approx(0.70)
    assert a.scores["edit_confidence_factors"]["scope_control"] == pytest.approx(0.84)
    assert a.scores["edit_confidence"] < ab.build_assessment(_worked_example_input()).scores[
        "edit_confidence"
    ]


def test_builder_file_metadata_penalties_never_zero_confidence_factors() -> None:
    factors = dict(_worked_example_input().edit_confidence_factors)
    factors["evidence_quality"] = 0.15
    factors["scope_control"] = 0.08
    inp = replace(
        _worked_example_input(),
        edit_confidence_factors=factors,
        architecture_evidence=m.ArchitectureEvidence(
            god_node_score=0.95,
            cycle_participation=True,
            bridge_centrality=0.8,
            domain_entrypoint=True,
        ),
        fanin_evidence=m.FanInEvidence(
            resolution_method="location",
            graph_freshness="fresh",
            graph_file_error_count=3,
            graph_file_size_bytes=240_000,
            graph_file_node_count=750,
        ),
    )

    a = ab.build_assessment(inp)

    assert a.scores["edit_confidence_factors"]["evidence_quality"] > 0.0
    assert a.scores["edit_confidence_factors"]["scope_control"] > 0.0


def test_builder_absent_codegraph_file_metadata_leaves_confidence_unchanged() -> None:
    inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(resolution_method="location", graph_freshness="fresh"),
    )

    a = ab.build_assessment(inp)

    assert a.scores["edit_confidence_factors"]["evidence_quality"] == pytest.approx(0.78)
    assert a.scores["edit_confidence_factors"]["scope_control"] == pytest.approx(0.92)


def test_builder_carries_symbol_scope_evidence() -> None:
    a = ab.build_assessment(_worked_example_input())
    sse = a.scores["symbol_scope_evidence"]
    assert sse["max_change_kind"] == "BEHAVIORAL"
    assert sse["consequential_symbol_changed"] is False
    assert sse["scope_basis"] == "symbol"  # parsed_patch_available -> symbol


def test_builder_explicit_variance_takes_precedence_one() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.scores["variance_source"] == "explicit"
    assert a.scores["utility_sd"] == pytest.approx(0.06)


def test_builder_uses_first_order_variance_when_no_explicit_breakdown() -> None:
    # AD-5 precedence 2: with no explicit breakdown, the builder must compute first-order propagation
    # from the component variances (benefit_variance from the benefit model), NOT fall to cold-start.
    from dataclasses import replace
    inp = replace(_worked_example_input(), variance_breakdown=None)
    a = ab.build_assessment(inp)
    assert a.scores["variance_source"] == "first_order"
    # contribution from benefit: p_success^2 * benefit_variance (projected 0.04)
    assert a.scores["variance_breakdown"]["benefit"] == pytest.approx((0.74**2) * 0.04)


def test_builder_scope_basis_file_fallback_when_not_parsed() -> None:
    from dataclasses import replace
    from pebra.core import models as m
    inp = replace(
        _worked_example_input(),
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=["src/auth.py::validate_login"],
            max_change_kind="UNKNOWN",
        ),
    )
    a = ab.build_assessment(inp)
    assert a.scores["symbol_scope_evidence"]["scope_basis"] == "file_fallback"


def test_builder_scope_basis_graph_semantic_for_codegraph_semantic_tier() -> None:
    inp = replace(
        _worked_example_input(),
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=["src/a.ts::f"],
            max_change_kind="CONTRACT",
            structure_tier="codegraph_semantic",
        ),
    )
    a = ab.build_assessment(inp)
    assert a.scores["symbol_scope_evidence"]["scope_basis"] == "graph_semantic"


def test_builder_scope_basis_unknown_fallback_when_no_symbols() -> None:
    from dataclasses import replace
    from pebra.core import models as m
    inp = replace(
        _worked_example_input(),
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False, changed_symbols=[], max_change_kind="UNKNOWN"
        ),
    )
    a = ab.build_assessment(inp)
    assert a.scores["symbol_scope_evidence"]["scope_basis"] == "unknown_fallback"
