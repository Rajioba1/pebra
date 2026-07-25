"""Architecture §5/§7, AD-4 — assessment_builder: pure factory AssessmentInput -> scored Assessment.

It receives gathered evidence (never calls a port), composes the pure score modules, sets
action_status=pending (AD-4), and reproduces the spec §10 worked-example score set.
"""

from __future__ import annotations

import hashlib
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


def test_builder_surfaces_per_file_benefit_breakdown() -> None:
    evidence = m.BenefitDeltaEvidence(
        source_type="measured",
        deltas={"complexity_delta": -2.0, "maintainability_index_delta": 4.0},
        file_deltas={
            "src/a.py": {
                "complexity_delta": -2.0,
                "maintainability_index_delta": 4.0,
                "exposure_weight": 3.0,
            }
        },
    )
    scores = ab.build_assessment(
        replace(_worked_example_input(), benefit_delta_evidence=evidence)
    ).scores

    assert scores["benefit_file_deltas"] == evidence.file_deltas


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


def test_builder_persists_resolved_graph_identity_for_hotspot_replay() -> None:
    # The dashboard maps a stored assessment back onto graph nodes by qualified name (the same identity
    # the verify path re-resolves by). Aggregate counts alone can't do that, so the builder must carry
    # the resolved qualified names + file paths into the persisted symbol_fanin dict.
    inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            symbol_fan_in_percentile=0.5,
            symbol_caller_count=3,
            resolution_method="location",
            graph_freshness="fresh",
            resolved_qualified_names=("Gamma::Gamma", "Gamma::LogGamma"),
            resolved_file_paths=("src/Gamma.cs",),
        ),
    )

    sse = ab.build_assessment(inp).scores["symbol_scope_evidence"]

    assert sse["symbol_fanin"]["resolved_qualified_names"] == ["Gamma::Gamma", "Gamma::LogGamma"]
    assert sse["symbol_fanin"]["resolved_file_paths"] == ["src/Gamma.cs"]


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


def test_builder_uses_learned_benefit_variance_override_when_present() -> None:
    inp = replace(
        _worked_example_input(),
        variance_breakdown=None,
        benefit_variance_override=0.0015,
    )

    scores = ab.build_assessment(inp).scores

    assert scores["variance_source"] == "first_order"
    assert scores["benefit_breakdown"].benefit_variance == pytest.approx(0.0015)
    assert scores["variance_breakdown"]["benefit"] == pytest.approx((0.74**2) * 0.0015)


def test_builder_consumes_benefit_variance_from_applied_snapshot() -> None:
    from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot

    inp = replace(_worked_example_input(), variance_breakdown=None)
    learned = apply_snapshot(
        inp,
        SnapshotBundle(
            snapshot_id="rs_1",
            facts=(
                SnapshotFact(
                    fact_id="lrf_1",
                    target_type="benefit_binary",
                    target_name="immediate_benefit_realized",
                    scope_kind="global",
                    scope_value="",
                    specificity_rank=0,
                    value=0.8,
                    sample_size=50,
                    created_at="2026-07-22T00:00:00Z",
                    calibration_method="observed_rate_v1",
                    variance=0.0005,
                    aleatoric_variance=0.001,
                ),
            ),
        ),
    )

    scores = ab.build_assessment(learned).scores

    assert scores["benefit_breakdown"].benefit_variance == pytest.approx(0.0015)
    assert scores["variance_breakdown"]["benefit"] == pytest.approx((0.74**2) * 0.0015)


def test_builder_propagates_learned_event_probability_variance() -> None:
    inp = replace(
        _worked_example_input(),
        variance_breakdown=None,
        events=[
            {"event": "test_regression", "p_event": 0.20, "elicited_disutility": 0.50},
        ],
        event_probability_variances={"test_regression": 0.01},
    )

    scores = ab.build_assessment(inp).scores

    # Var(p*d) ~= d^2 Var(p) + p^2 Var(d), retaining the cold-start disutility variance.
    assert scores["variance_breakdown"]["event_losses"] == pytest.approx(
        (0.50**2) * 0.01 + (0.20**2) * 0.0025
    )


def test_builder_applies_cold_start_variance_to_every_event() -> None:
    inp = replace(
        _worked_example_input(),
        variance_breakdown=None,
        events=[
            {"event": "test_regression", "p_event": 0.20, "elicited_disutility": 0.50},
            {"event": "review_burden", "p_event": 0.30, "elicited_disutility": 0.40},
        ],
        event_probability_variances={},
    )

    scores = ab.build_assessment(inp).scores

    expected = (
        (0.50**2) * 0.0025
        + (0.20**2) * 0.0025
        + (0.40**2) * 0.0025
        + (0.30**2) * 0.0025
    )
    assert scores["variance_breakdown"]["event_losses"] == pytest.approx(expected)


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


def test_patch_bound_graph_fact_reduces_only_owned_graph_event_with_nonzero_floor() -> None:
    patch = "--- a/src/api.ts\n+++ b/src/api.ts\n@@ -1 +1 @@\n-old\n+new\n"
    base = _worked_example_input()
    inp = replace(
        base,
        action=replace(base.action, proposed_patch=patch),
        events=[{
            "event": "public_api_break",
            "risk_source": "graph_modify_risk",
            "owner_node_ids": ["owner-1"],
            "p_event": 0.45,
            "elicited_disutility": 0.80,
        }],
        candidate_graph_risk_evidence=m.CandidateGraphRiskEvidence(
            status="available",
            provider="materialized_codegraph",
            verified_patch_hash=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            facts=(m.ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="graph_modify_risk",
                owner_node_ids=("owner-1",),
            ),),
        ),
    )

    result = ab.build_assessment(inp)

    assert result.scores["expected_loss"] == pytest.approx(0.126)
    assert result.scores["verified_risk_events_removed"] == []
    assert result.scores["risk_probability_updates"][0]["original_probability"] == 0.45
    assert result.scores["risk_probability_updates"][0]["revised_probability"] == pytest.approx(
        0.1575
    )
    assert result.scores["risk_probability_updates"][0]["probability_multiplier"] == 0.35


def test_unbound_graph_fact_cannot_reduce_revision_risk() -> None:
    patch = "--- a/src/api.ts\n+++ b/src/api.ts\n@@ -1 +1 @@\n-old\n+new\n"
    base = _worked_example_input()
    inp = replace(
        base,
        action=replace(base.action, proposed_patch=patch),
        events=[{
            "event": "public_api_break",
            "risk_source": "graph_modify_risk",
            "owner_node_ids": ["owner-1"],
            "p_event": 0.45,
            "elicited_disutility": 0.80,
        }],
        candidate_graph_risk_evidence=m.CandidateGraphRiskEvidence(
            status="available",
            verified_patch_hash="wrong",
            facts=(m.ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="graph_modify_risk",
                owner_node_ids=("owner-1",),
            ),),
        ),
    )

    result = ab.build_assessment(inp)

    assert result.scores["expected_loss"] == pytest.approx(0.36)
    assert result.scores["verified_risk_events_removed"] == []
    assert result.scores["risk_probability_updates"] == []

def _owner(name: str, *witnesses: m.ImpactWitness) -> m.OwnerRiskEvidence:
    return m.OwnerRiskEvidence(
        node_id=f"id:{name}",
        qualified_name=name,
        impact_witnesses=witnesses,
    )


def test_project_impact_witnesses_serializes_without_node_ids() -> None:
    owners = (
        _owner(
            "pkg.changed",
            m.ImpactWitness(
                impacted_node_id="nid:1",
                qualified_name="pkg.caller",
                file_path="src\\caller.py",
                line=42,
                column=7,
                edge_kind="calls",
                depth=1,
                location_source="edge_site",
            ),
        ),
    )
    projected = ab.project_impact_witnesses(owners)
    assert projected == [
        {
            "owner_qualified_name": "pkg.changed",
            "dependent_qualified_name": "pkg.caller",
            "file_path": "src/caller.py",
            "line": 42,
            "column": 7,
            "edge_kind": "calls",
            "depth": 1,
            "location_source": "edge_site",
        }
    ]
    assert "nid:1" not in str(projected)
    assert "impacted_node_id" not in projected[0]


def test_builder_omits_impact_witnesses_when_none_retained() -> None:
    assessment = ab.build_assessment(
        replace(
            _worked_example_input(),
            fanin_evidence=m.FanInEvidence(
                resolution_method="location",
                graph_freshness="fresh",
                owner_risk=(),
            ),
        )
    )
    fanin = assessment.scores["symbol_scope_evidence"]["symbol_fanin"]
    assert "impact_witnesses" not in fanin


def test_builder_surfaces_capped_deterministic_impact_witnesses() -> None:
    witnesses = [
        m.ImpactWitness(
            impacted_node_id=f"n{i}",
            qualified_name=f"pkg.dep{i}",
            file_path=f"src/d{i}.py",
            line=10 + i,
            column=i,
            edge_kind="calls",
            depth=1,
            location_source="edge_site",
        )
        for i in range(1, 7)
    ]
    owners = (
        m.OwnerRiskEvidence(
            node_id="owner:a",
            qualified_name="pkg.changed",
            impact_witnesses=tuple(witnesses),
        ),
    )
    assessment = ab.build_assessment(
        replace(
            _worked_example_input(),
            fanin_evidence=m.FanInEvidence(
                resolution_method="location",
                graph_freshness="fresh",
                owner_risk=owners,
            ),
        )
    )
    projected = assessment.scores["symbol_scope_evidence"]["symbol_fanin"]["impact_witnesses"]
    assert len(projected) == 5
    assert [row["dependent_qualified_name"] for row in projected] == [
        "pkg.dep1", "pkg.dep2", "pkg.dep3", "pkg.dep4", "pkg.dep5",
    ]


def test_builder_impact_witness_order_is_stable_across_owner_order() -> None:
    w1 = m.ImpactWitness(
        impacted_node_id="a",
        qualified_name="pkg.a",
        file_path="src/a.py",
        line=1,
        column=1,
        edge_kind="calls",
        depth=1,
        location_source="edge_site",
    )
    w2 = m.ImpactWitness(
        impacted_node_id="b",
        qualified_name="pkg.b",
        file_path="src/b.py",
        line=2,
        column=2,
        edge_kind="calls",
        depth=1,
        location_source="edge_site",
    )
    forward = (
        m.OwnerRiskEvidence(node_id="o2", qualified_name="pkg.z", impact_witnesses=(w2,)),
        m.OwnerRiskEvidence(node_id="o1", qualified_name="pkg.a_owner", impact_witnesses=(w1,)),
    )
    reverse = tuple(reversed(forward))
    a = ab.project_impact_witnesses(forward)
    b = ab.project_impact_witnesses(reverse)
    assert a == b
    assert [row["owner_qualified_name"] for row in a] == ["pkg.a_owner", "pkg.z"]


def test_builder_impact_witness_allows_missing_line_column() -> None:
    owners = (
        m.OwnerRiskEvidence(
            node_id="o",
            qualified_name="pkg.changed",
            impact_witnesses=(
                m.ImpactWitness(
                    impacted_node_id="d",
                    qualified_name="pkg.dep",
                    file_path="src/dep.py",
                    line=None,
                    column=None,
                    edge_kind="",
                    depth=2,
                    location_source="node_definition",
                ),
            ),
        ),
    )
    projected = ab.project_impact_witnesses(owners)
    assert projected[0]["line"] is None
    assert projected[0]["column"] is None
    assert projected[0]["depth"] == 2


def test_impact_witnesses_do_not_change_decision_fingerprint() -> None:
    from pebra.core import decision_engine as de

    base = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            resolution_method="location",
            graph_freshness="fresh",
            symbol_fan_in_percentile=0.9,
            symbol_caller_count=5,
            modify_impact_count=5,
            owner_risk=(
                m.OwnerRiskEvidence(node_id="o", qualified_name="pkg.changed"),
            ),
        ),
    )
    with_w = replace(
        base,
        fanin_evidence=replace(
            base.fanin_evidence,
            owner_risk=(
                m.OwnerRiskEvidence(
                    node_id="o",
                    qualified_name="pkg.changed",
                    impact_witnesses=(
                        m.ImpactWitness(
                            impacted_node_id="c",
                            qualified_name="pkg.caller",
                            file_path="src/c.py",
                            line=9,
                            column=1,
                            edge_kind="calls",
                            depth=1,
                            location_source="edge_site",
                        ),
                    ),
                ),
            ),
        ),
    )

    def fingerprint(result):
        return {
            "recommended_decision": result.recommended_decision,
            "requires_confirmation": result.requires_confirmation,
            "risk_mode": result.risk_mode,
            "action_status": result.action_status,
            "gates_fired": result.gates_fired,
            "decision_reason": result.decision_reason,
            "expected_loss": result.scores["expected_loss"],
            "benefit": result.scores["benefit"],
            "expected_utility": result.scores["expected_utility"],
            "utility_sd": result.scores["utility_sd"],
            "rau": result.scores["rau"],
        }

    a0 = ab.build_assessment(base)
    a1 = ab.build_assessment(with_w)
    r0 = de.decide(a0)
    r1 = de.decide(a1)
    assert fingerprint(r0) == fingerprint(r1)
    assert "impact_witnesses" not in (a0.scores["symbol_scope_evidence"]["symbol_fanin"] or {})
    assert a1.scores["symbol_scope_evidence"]["symbol_fanin"]["impact_witnesses"]
