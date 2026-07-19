from __future__ import annotations

from pathlib import Path
import re

import pytest

from pebra.adapters.gate_check_adapter import GateDecision
from pebra.core.constants import Decision
from pebra.core.gate_contract import (
    ALLOWED_PERMISSION_TIERS,
    ALLOWED_RISK_DECISIONS,
    GATE_SCHEMA_VERSION,
    GatePermission,
    GateRiskSummary,
    GateTier,
)


def _summary(decision: Decision) -> GateRiskSummary:
    return GateRiskSummary(
        decision=decision,
        expected_loss=0.61,
        benefit=0.34,
        rau=-0.27,
    )


def test_gate_contract_covers_every_tier():
    declared = {tier for tiers in ALLOWED_PERMISSION_TIERS.values() for tier in tiers}
    assert declared == set(GateTier)


@pytest.mark.parametrize(
    ("permission", "tier"),
    [
        (permission, tier)
        for permission, tiers in ALLOWED_PERMISSION_TIERS.items()
        for tier in tiers
    ],
)
def test_declared_pairs_construct(permission, tier):
    reason = (
        None if permission is GatePermission.CONTINUE
        else "This exact candidate is held; follow the required next action."
    )
    decision = GateDecision(permission, tier, reason=reason)
    assert decision.as_dict()["schema_version"] == GATE_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("permission", "tier"),
    [
        (permission, tier)
        for permission in GatePermission
        for tier in GateTier
        if tier not in ALLOWED_PERMISSION_TIERS[permission]
    ],
)
def test_every_undeclared_pair_is_rejected(permission, tier):
    with pytest.raises(ValueError, match="undeclared gate permission/tier pair"):
        GateDecision(permission, tier)


def test_experiment_positive_control_is_not_a_production_tier():
    assert "positive_control" not in {tier.value for tier in GateTier}


@pytest.mark.parametrize("field", ("expected_loss", "benefit", "rau"))
@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf"), True))
def test_risk_summary_rejects_non_finite_or_boolean_numbers(field, value):
    values = {"expected_loss": 0.61, "benefit": 0.34, "rau": -0.27}
    values[field] = value
    with pytest.raises(ValueError, match="finite"):
        GateRiskSummary(decision=Decision.REVISE_SAFER, **values)


def test_risk_summary_normalizes_integer_numbers_to_floats():
    summary = GateRiskSummary(
        decision=Decision.REVISE_SAFER,
        expected_loss=1,
        benefit=0,
        rau=-1,
    )

    assert summary.expected_loss == 1.0
    assert summary.benefit == 0.0
    assert summary.rau == -1.0
    assert all(
        isinstance(value, float)
        for value in (summary.expected_loss, summary.benefit, summary.rau)
    )


@pytest.mark.parametrize("value", (10**1000, -(10**1000)))
def test_risk_summary_translates_oversized_integer_overflow_to_value_error(value):
    with pytest.raises(ValueError, match="finite"):
        GateRiskSummary(
            decision=Decision.REVISE_SAFER,
            expected_loss=value,
            benefit=0.34,
            rau=-0.27,
        )


@pytest.mark.parametrize(
    ("permission", "tier", "assessment_decision"),
    [
        (permission, tier, assessment_decision)
        for permission in GatePermission
        for tier in GateTier
        for assessment_decision in Decision
    ],
)
def test_risk_decision_matrix_is_complete(permission, tier, assessment_decision):
    reason = None if permission is GatePermission.CONTINUE else "Take the required next action."
    allowed = assessment_decision in ALLOWED_RISK_DECISIONS.get((permission, tier), frozenset())
    if allowed:
        decision = GateDecision(
            permission,
            tier,
            reason=reason,
            risk_summary=_summary(assessment_decision),
            matched_assessment_id="asm_1",
        )
        assert decision.risk_summary.decision is assessment_decision
    else:
        with pytest.raises(ValueError):
            GateDecision(
                permission,
                tier,
                reason=reason,
                risk_summary=_summary(assessment_decision),
                matched_assessment_id="asm_1",
            )


@pytest.mark.parametrize(
    ("permission", "tier"),
    [
        (permission, tier)
        for permission, tiers in ALLOWED_PERMISSION_TIERS.items()
        for tier in tiers
        if (permission, tier) not in ALLOWED_RISK_DECISIONS
    ],
)
def test_non_consulted_pairs_reject_any_risk_summary(permission, tier):
    reason = None if permission is GatePermission.CONTINUE else "Take the required next action."
    with pytest.raises(ValueError, match="risk summary decision"):
        GateDecision(
            permission,
            tier,
            reason=reason,
            risk_summary=_summary(Decision.PROCEED),
            matched_assessment_id="asm_1",
        )


@pytest.mark.parametrize("permission", (GatePermission.RETURN_CANDIDATE, GatePermission.REQUEST_HUMAN))
@pytest.mark.parametrize("reason", (None, "", " \t"))
def test_restrictive_decisions_require_nonblank_actionable_reason(permission, reason):
    tier = (
        GateTier.CONSULTED_REVIEW
        if permission is GatePermission.REQUEST_HUMAN
        else GateTier.MUST_CONSULT
    )
    with pytest.raises(ValueError, match="actionable reason"):
        GateDecision(permission, tier, reason=reason)


def test_consulted_review_distinguishes_reject_from_ask_human():
    denied = GateDecision(
        GatePermission.RETURN_CANDIDATE,
        GateTier.CONSULTED_REVIEW,
        reason="Choose another route.",
        risk_summary=_summary(Decision.REJECT),
        matched_assessment_id="asm_1",
    )
    asked = GateDecision(
        GatePermission.REQUEST_HUMAN,
        GateTier.CONSULTED_REVIEW,
        reason="Run the bound review workflow.",
        risk_summary=_summary(Decision.ASK_HUMAN),
        matched_assessment_id="asm_1",
    )
    assert denied.risk_summary.decision is Decision.REJECT
    assert asked.risk_summary.decision is Decision.ASK_HUMAN


@pytest.mark.parametrize("matched_id", (None, "", " ", "asm_0", "asm_-1", "asm_01", "asm_x", "asm_1x"))
def test_risk_summary_requires_exact_positive_assessment_id(matched_id):
    with pytest.raises(ValueError, match="exact matched assessment id"):
        GateDecision(
            GatePermission.CONTINUE,
            GateTier.CONSULTED,
            risk_summary=_summary(Decision.PROCEED),
            matched_assessment_id=matched_id,
        )


def test_host_attribution_is_retained_internally_and_optional_on_wire():
    decision = GateDecision(
        GatePermission.CONTINUE,
        GateTier.CONSULTED,
        risk_summary=_summary(Decision.PROCEED),
        matched_assessment_id="asm_1",
    )
    assert decision.matched_assessment_id == "asm_1"
    assert "matched_assessment_id" not in decision.as_dict(include_host_metadata=False)
    assert decision.as_dict(include_host_metadata=True)["matched_assessment_id"] == "asm_1"
    assert decision.as_dict()["risk_summary"] == {
        "decision": "proceed",
        "expected_loss": 0.61,
        "benefit": 0.34,
        "rau": -0.27,
    }


def test_gate_contract_document_has_exact_allowed_pair_set():
    body = (Path(__file__).parents[2] / "docs" / "GATE_CONTRACT.md").read_text(encoding="utf-8")
    documented = set(re.findall(
        r"^\| `(allow|deny|ask)` \| `([^`]+)` \|",
        body,
        flags=re.MULTILINE,
    ))
    expected = {
        (permission.value, tier.value)
        for permission, tiers in ALLOWED_PERMISSION_TIERS.items()
        for tier in tiers
    }
    assert documented == expected
