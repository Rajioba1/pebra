"""Architecture §5 / AD-28 — benefit model. Pure: receives collected deltas, computes benefit.

Phase 0 milestone: in ``projected`` mode (no real patch metrics) benefit == immediate_benefit and
projected maintainability improvement earns no gate-driving credit (reproduces worked-example 0.82).
"""

from __future__ import annotations

import math

import pytest

from pebra.core import benefit_model as bm


def test_projected_mode_benefit_equals_immediate_benefit() -> None:
    result = bm.resolve_benefit(
        immediate_benefit=0.82,
        deltas={},  # all-zero / absent
        source_type="projected",
        future_change_exposure=0.0,
    )
    assert result.benefit == pytest.approx(0.82)
    assert result.immediate_benefit == pytest.approx(0.82)
    assert result.credited_maintainability_gain == pytest.approx(0.0)


def test_projected_maintainability_improvement_earns_no_credit() -> None:
    # even with positive (good) deltas, projected mode credits nothing
    result = bm.resolve_benefit(
        immediate_benefit=0.50,
        deltas={"complexity_delta": -0.4, "testability_delta": 0.4},
        source_type="projected",
        future_change_exposure=1.0,
    )
    assert result.benefit == pytest.approx(0.50)
    assert result.credited_maintainability_gain == pytest.approx(0.0)


def test_measured_good_delta_raises_benefit_above_immediate() -> None:
    # measured: reduced complexity (good direction) + raised testability earns positive credit
    result = bm.resolve_benefit(
        immediate_benefit=0.50,
        deltas={"complexity_delta": -0.4, "testability_delta": 0.4},
        source_type="measured",
        future_change_exposure=1.0,
    )
    assert result.benefit > 0.50
    assert result.credited_maintainability_gain > 0.0


def test_benefit_monotonic_worse_maintainability_never_raises_benefit() -> None:
    # worsening coupling (bad direction: higher is worse) must not increase benefit
    better = bm.resolve_benefit(
        immediate_benefit=0.50,
        deltas={"coupling_delta": -0.2},
        source_type="measured",
        future_change_exposure=1.0,
    )
    worse = bm.resolve_benefit(
        immediate_benefit=0.50,
        deltas={"coupling_delta": 0.2},
        source_type="measured",
        future_change_exposure=1.0,
    )
    assert worse.benefit <= better.benefit


def test_measured_metric_units_are_bounded_before_entering_utility() -> None:
    result = bm.resolve_benefit(
        immediate_benefit=0.50,
        deltas={"complexity_delta": 1.0, "maintainability_index_delta": 0.0},
        source_type="measured",
        future_change_exposure=0.992,
    )

    assert -0.30 < result.credited_maintainability_gain < 0.0
    assert result.benefit > 0.0


def test_extreme_measured_deltas_cannot_create_unbounded_utility() -> None:
    gain = bm.maintainability_gain(
        {"complexity_delta": -1_000_000.0, "maintainability_index_delta": 1_000_000.0},
        future_change_exposure=0.7,
    )

    assert 0.0 < gain <= 0.7


@pytest.mark.parametrize("exposure", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_exposure_earns_no_maintainability_credit(exposure: float) -> None:
    gain = bm.maintainability_gain(
        {"complexity_delta": -1.0}, future_change_exposure=exposure
    )

    assert math.isfinite(gain)
    assert gain == 0.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_immediate_benefit_fails_to_zero(value: float) -> None:
    result = bm.resolve_benefit(value, {}, "projected")

    assert result.immediate_benefit == 0.0
    assert result.benefit == 0.0


def test_total_benefit_is_bounded_to_unit_utility() -> None:
    high = bm.resolve_benefit(100.0, {"complexity_delta": -100.0}, "measured", 1.0)
    low = bm.resolve_benefit(0.1, {"complexity_delta": 100.0}, "measured", 1.0)

    assert high.benefit == 1.0
    assert low.benefit == 0.0


def test_projected_mode_widens_variance_vs_measured() -> None:
    projected = bm.resolve_benefit(
        immediate_benefit=0.82, deltas={}, source_type="projected", future_change_exposure=0.0
    )
    measured = bm.resolve_benefit(
        immediate_benefit=0.82,
        deltas={"complexity_delta": -0.1},
        source_type="measured",
        future_change_exposure=1.0,
    )
    assert projected.benefit_variance > measured.benefit_variance


def test_projected_mode_uses_spec_variance_floor() -> None:
    projected = bm.resolve_benefit(
        immediate_benefit=0.82,
        deltas={},
        source_type="projected",
        future_change_exposure=0.0,
    )
    assert projected.benefit_variance == pytest.approx(0.04)
