"""Architecture §5/§7, AD-4 — assessment_builder: pure factory AssessmentInput -> scored Assessment.

It receives gathered evidence (never calls a port), composes the pure score modules, sets
action_status=pending (AD-4), and reproduces the spec §10 worked-example score set.
"""

from __future__ import annotations

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


def test_builder_uses_tighter_c3_threshold_as_effective() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.scores["effective_threshold"] == pytest.approx(0.20)
    assert a.scores["budget_threshold_key"] == "c3_max_expected_loss_without_human"


def test_builder_confidence_band_high() -> None:
    a = ab.build_assessment(_worked_example_input())
    assert a.confidence_band == "high"


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
    # contribution from benefit: p_success^2 * benefit_variance (projected 0.0064)
    assert a.scores["variance_breakdown"]["benefit"] == pytest.approx((0.74**2) * 0.0064)


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
