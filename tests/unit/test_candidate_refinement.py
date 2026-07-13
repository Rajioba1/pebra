from pebra.core.candidate_refinement import (
    CandidateRankInput,
    apply_scoped_adjustments,
    rank_candidates,
)
from pebra.core.models import CandidateGraphRiskEvidence, ScopedGraphRiskFact


def test_scoped_adjustment_is_patch_bound_and_event_specific() -> None:
    events = [
        {
            "event": "public_api_break",
            "risk_source": "modify_graph",
            "owner_node_ids": ["owner-1"],
            "p_event": 0.60,
            "elicited_disutility": 0.8,
        },
        {"event": "dependency_break", "p_event": 0.40, "elicited_disutility": 0.7},
    ]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="abc",
        provider="materialized_codegraph",
        facts=(
            ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="modify_graph",
                owner_node_ids=("owner-1",),
            ),
        ),
    )

    adjusted, applied = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted[0]["p_event"] == 0.39
    assert adjusted[1] == events[1]
    assert applied == ["public_api_break"]


def test_unbound_refinement_cannot_reduce_risk() -> None:
    events = [{
        "event": "public_api_break",
        "risk_source": "modify_graph",
        "owner_node_ids": ["owner-1"],
        "p_event": 0.60,
        "elicited_disutility": 0.8,
    }]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="different",
        facts=(ScopedGraphRiskFact(
            fact_kind="exported_binding_continuity",
            event="public_api_break",
            risk_source="modify_graph",
            owner_node_ids=("owner-1",),
        ),),
    )

    adjusted, applied = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted == events
    assert applied == []


def test_non_finite_fact_confidence_cannot_reduce_risk() -> None:
    events = [{
        "event": "public_api_break", "risk_source": "modify_graph",
        "owner_node_ids": ["owner-1"], "p_event": 0.60, "elicited_disutility": 0.8,
    }]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="abc",
        facts=(ScopedGraphRiskFact(
            fact_kind="exported_binding_continuity",
            event="public_api_break",
            risk_source="modify_graph",
            owner_node_ids=("owner-1",),
            confidence=float("nan"),
        ),),
    )

    adjusted, applied = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted == events
    assert applied == []


def test_probability_floor_is_never_crossed() -> None:
    events = [{
        "event": "public_api_break",
        "risk_source": "modify_graph",
        "owner_node_ids": ["owner"],
        "p_event": 0.06,
        "elicited_disutility": 0.8,
    }]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="abc",
        facts=(
            ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="modify_graph",
                owner_node_ids=("owner",),
            ),
        ),
    )

    adjusted, _ = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted[0]["p_event"] == 0.05


def test_partial_owner_or_wrong_source_never_reduces_event() -> None:
    events = [{
        "event": "public_api_break",
        "risk_source": "graph_modify_risk",
        "owner_node_ids": ["owner-1", "owner-2"],
        "p_event": 0.60,
        "elicited_disutility": 0.8,
    }]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="abc",
        facts=(ScopedGraphRiskFact(
            fact_kind="exported_binding_continuity",
            event="public_api_break",
            risk_source="graph_modify_risk",
            owner_node_ids=("owner-1",),
        ),),
    )

    adjusted, applied = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted == events
    assert applied == []


def test_request_event_without_graph_owner_identity_is_never_adjusted() -> None:
    events = [{"event": "public_api_break", "p_event": 0.60, "elicited_disutility": 0.8}]
    evidence = CandidateGraphRiskEvidence(
        status="available",
        verified_patch_hash="abc",
        facts=(ScopedGraphRiskFact(
            fact_kind="exported_binding_continuity",
            event="public_api_break",
            risk_source="graph_modify_risk",
            owner_node_ids=("owner-1",),
        ),),
    )

    adjusted, applied = apply_scoped_adjustments(events, evidence, patch_hash="abc")

    assert adjusted == events
    assert applied == []


def _candidate(
    action_id: str,
    *,
    benefit: float,
    loss: float,
    rau: float,
    exposure: float,
    files: int,
    order: int,
    needs_refinement: bool = True,
) -> CandidateRankInput:
    return CandidateRankInput(
        action_id=action_id,
        eligible=True,
        needs_refinement=needs_refinement,
        benefit=benefit,
        expected_loss=loss,
        rau=rau,
        cumulative_exposure=exposure,
        file_count=files,
        owner_count=1,
        domain_count=1,
        resolution_coverage=1.0,
        patch_hash=f"{order:064x}",
    )


def test_pre_refinement_dominance_only_orders_and_never_discards() -> None:
    better = _candidate("better", benefit=0.8, loss=0.2, rau=0.3, exposure=0.4, files=1, order=1)
    dominated = _candidate(
        "dominated", benefit=0.7, loss=0.3, rau=0.1, exposure=0.5, files=2, order=0
    )

    ranked = rank_candidates([dominated, better])

    assert [candidate.action_id for candidate in ranked] == ["better", "dominated"]


def test_ranking_is_deterministic_and_patch_hash_is_final_tiebreak() -> None:
    first = _candidate("first", benefit=0.8, loss=0.2, rau=0.3, exposure=0.4, files=1, order=0)
    second = _candidate("second", benefit=0.8, loss=0.2, rau=0.3, exposure=0.4, files=1, order=1)

    ranked = rank_candidates([second, first])

    assert [candidate.action_id for candidate in ranked] == ["first", "second"]


def test_candidate_that_already_proceeds_does_not_need_materialized_refinement() -> None:
    candidate = _candidate(
        "safe", benefit=0.8, loss=0.1, rau=0.4, exposure=0.2, files=1, order=0,
        needs_refinement=False,
    )

    assert rank_candidates([candidate]) == []
