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


def test_cold_start_default_when_nothing_supplied() -> None:
    breakdown, total, source = sn.resolve_utility_variance(explicit_breakdown=None)
    assert source == "cold_start"
    assert total > 0
