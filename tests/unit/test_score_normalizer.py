"""Architecture §5 / AD-5 — ScoreNormalizer: utility-variance precedence + range mapping."""

from __future__ import annotations

import pytest

from pebra.core import score_normalizer as sn


def test_clamp_unit_keeps_probabilities_in_range() -> None:
    assert sn.clamp_unit(1.4) == 1.0
    assert sn.clamp_unit(-0.2) == 0.0
    assert sn.clamp_unit(0.5) == 0.5


def test_explicit_variance_breakdown_takes_precedence() -> None:
    # AD-5 precedence 1: explicit variance supplied with the input wins.
    explicit = {
        "p_success": 0.0016,
        "benefit": 0.0004,
        "event_losses": 0.0009,
        "review_cost": 0.0004,
        "scenario_variance": 0.0003,
    }
    breakdown, total, source = sn.resolve_utility_variance(explicit_breakdown=explicit)
    assert source == "explicit"
    assert total == pytest.approx(0.0036)
    assert breakdown == explicit


def test_first_order_propagation_when_no_explicit_breakdown() -> None:
    # AD-5 precedence 2: compute first-order contribution terms from component variances.
    breakdown, total, source = sn.resolve_utility_variance(
        explicit_breakdown=None,
        benefit=0.82,
        p_success=0.74,
        var_p_success=0.0016 / (0.82**2),
        var_benefit=0.0004 / (0.74**2),
        var_review_cost=0.0004,
        event_variance=0.0009,
        scenario_variance=0.0003,
    )
    assert source == "first_order"
    assert breakdown["p_success"] == pytest.approx(0.0016)
    assert breakdown["benefit"] == pytest.approx(0.0004)
    assert total == pytest.approx(0.0036)


def test_confidence_maps_to_variance_when_component_variance_absent() -> None:
    assert sn.variance_from_confidence(1.0) == pytest.approx(0.0)
    assert sn.variance_from_confidence(0.6) == pytest.approx(0.04)

    breakdown, _total, source = sn.resolve_utility_variance(
        explicit_breakdown=None,
        benefit=0.80,
        p_success=0.75,
        var_p_success=None,
        var_benefit=None,
        var_review_cost=None,
        p_success_confidence=0.60,
        benefit_confidence=0.80,
        review_cost_confidence=0.90,
        event_confidence=0.70,
        disutility_confidence=0.70,
    )
    assert source == "first_order"
    assert breakdown["p_success"] == pytest.approx((0.80**2) * 0.04)
    assert breakdown["benefit"] == pytest.approx((0.75**2) * 0.01)
    assert breakdown["review_cost"] == pytest.approx(0.0025)
    assert breakdown["event_losses"] == pytest.approx(0.045)


def test_explicit_zero_component_variance_is_not_replaced_by_cold_start() -> None:
    breakdown, _total, source = sn.resolve_utility_variance(
        explicit_breakdown=None,
        benefit=0.80,
        p_success=0.75,
        var_p_success=0.0,
        var_benefit=0.0,
        var_review_cost=0.0,
        event_variance=0.0,
        scenario_variance=0.0,
    )

    assert source == "first_order"
    assert breakdown == {
        "p_success": 0.0,
        "benefit": 0.0,
        "event_losses": 0.0,
        "review_cost": 0.0,
        "scenario_variance": 0.0,
    }


def test_cold_start_default_when_nothing_supplied() -> None:
    breakdown, total, source = sn.resolve_utility_variance(explicit_breakdown=None)
    assert source == "cold_start"
    assert breakdown == {
        "p_success": 0.04,
        "benefit": 0.01,
        "event_losses": 0.005,
        "review_cost": 0.01,
        "scenario_variance": 0.0003,
    }
    assert total == pytest.approx(0.0653)
