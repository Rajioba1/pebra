"""Architecture §3/§7 — the IR dataclasses (AssessmentRequest / AssessmentInput / AssessmentResult).

The engine reads only AssessmentInput and returns only AssessmentResult (§3 invariant). These tests
pin the construction contract and the AD-8 single-action short form.
"""

from __future__ import annotations

from pebra.core import models as m
from pebra.core.constants import ActionStatus, Decision, RiskMode


def test_candidate_action_defaults() -> None:
    a = m.CandidateAction(id="a1", label="Patch validate_login only", action_type="edit")
    assert a.affected_symbols == []
    assert a.expected_files == []
    assert a.is_dependency_change is False
    assert a.is_schema_change is False
    assert a.is_migration is False


def test_single_action_short_form_builds_canonical_request() -> None:
    # AD-8: pebra_assess single-action short form builds the same AssessmentRequest object.
    req = m.AssessmentRequest.single_action(
        task="Fix failing login validation",
        action_id="a1",
        label="Patch validate_login only",
        action_type="edit",
    )
    assert req.task == "Fix failing login validation"
    assert len(req.candidate_actions) == 1
    assert req.candidate_actions[0].id == "a1"
    assert req.schema_version  # populated


def test_assessment_input_carries_everything_engine_needs() -> None:
    req = m.AssessmentRequest.single_action(
        task="t", action_id="a1", label="l", action_type="edit"
    )
    inp = m.AssessmentInput(
        request=req,
        action=req.candidate_actions[0],
        events=[{"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40}],
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
        thresholds={"c3_max_expected_loss_without_human": 0.20},
        repo_id="repo_local_example",
        repo_root="/abs/path",
    )
    assert inp.criticality_stage == "C3"
    assert inp.active_snapshot is None  # cold start, no learning in Phase 0
    assert inp.repo_id == "repo_local_example"


def test_fanin_evidence_defaults() -> None:
    # The language-agnostic per-symbol fan-in evidence (codegraph-backed). Defaults must describe
    # the cold/unresolved state: zero fan-in, unresolved, freshness unknown, no version stamps.
    ev = m.FanInEvidence()
    assert ev.symbol_fan_in_percentile == 0.0
    assert ev.symbol_caller_count == 0
    assert ev.resolution_method == "unresolved"
    assert ev.node_ids_resolved == ()
    assert ev.provider_version is None
    assert ev.index_version is None
    assert ev.graph_freshness == "unknown"
    assert ev.fallback_reason is None


def test_fanin_evidence_is_frozen() -> None:
    import dataclasses

    ev = m.FanInEvidence()
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        ev.symbol_fan_in_percentile = 0.5  # type: ignore[misc]


def test_assessment_input_defaults_fanin_evidence_to_none() -> None:
    req = m.AssessmentRequest.single_action(task="t", action_id="a1", label="l")
    inp = m.AssessmentInput(
        request=req,
        action=req.candidate_actions[0],
        events=[],
        p_success=0.7,
        immediate_benefit=0.8,
        review_cost=0.1,
        criticality_stage="C3",
        criticality_value=0.8,
        edit_confidence_factors={},
        thresholds={},
        repo_id="r",
        repo_root="/p",
    )
    assert inp.fanin_evidence is None


def test_assessment_result_holds_decision_and_scores() -> None:
    res = m.AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"rau": 0.31},
        repo_id="r",
        repo_root="/p",
    )
    assert res.recommended_decision is Decision.PROCEED
    assert res.requires_confirmation is True
    assert res.action_status is ActionStatus.PENDING
    assert res.high_risk_triggers == []
    assert res.gates_fired == []
