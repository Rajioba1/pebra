"""Milestone 4c — pure prediction-error math (AD-15). Standard scoring rules, hand-rolled in core
(no numpy/sklearn — forbidden in core). Binary targets: Brier + log-loss; continuous: squared error.
Residual is signed (actual - predicted)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pebra.core import prediction_error as pe
from pebra.core.constants import LOG_LOSS_CLIP_EPS


def test_brier_perfect_and_worst() -> None:
    assert pe.brier_score(1.0, 1) == 0.0
    assert pe.brier_score(1.0, 0) == 1.0
    assert pe.brier_score(0.5, 1) == pytest.approx(0.25)


def test_log_loss_clips_extremes_to_finite() -> None:
    # an overconfident-and-wrong prediction must not be -inf; clip uses LOG_LOSS_CLIP_EPS
    assert math.isfinite(pe.log_loss_single(0.0, 1))
    assert pe.log_loss_single(0.0, 1) == pytest.approx(-math.log(LOG_LOSS_CLIP_EPS))


def test_log_loss_perfect_is_zero() -> None:
    assert pe.log_loss_single(1.0, 1) == pytest.approx(0.0, abs=1e-9)


def test_residual_is_signed_actual_minus_predicted() -> None:
    assert pe.residual(0.6, 1) == pytest.approx(0.4)   # under-predicted
    assert pe.residual(0.6, 0) == pytest.approx(-0.6)  # over-predicted


def test_squared_error_symmetric() -> None:
    assert pe.squared_error(0.3, 0.7) == pytest.approx(pe.squared_error(0.7, 0.3))


def test_aggregates_over_pairs() -> None:
    assert pe.mean_brier([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    assert pe.mse([(0.0, 1.0), (1.0, 0.0)]) == pytest.approx(1.0)


def test_signed_bias_value() -> None:
    # residuals: (1-0.4)=0.6 and (0-0.4)=-0.4 -> mean 0.1
    assert pe.signed_bias([(0.4, 1), (0.4, 0)]) == pytest.approx(0.1)


def test_empty_aggregates_raise() -> None:
    for fn in (pe.mean_brier, pe.mean_log_loss, pe.mse, pe.signed_bias):
        with pytest.raises(ValueError):
            fn([])


@given(p=st.floats(min_value=0.0, max_value=1.0), y=st.integers(min_value=0, max_value=1))
def test_brier_in_unit_interval(p: float, y: int) -> None:
    assert 0.0 <= pe.brier_score(p, y) <= 1.0


@given(p=st.floats(min_value=0.0, max_value=1.0), y=st.integers(min_value=0, max_value=1))
def test_log_loss_non_negative_and_finite(p: float, y: int) -> None:
    v = pe.log_loss_single(p, y)
    assert v >= 0.0 and math.isfinite(v)
