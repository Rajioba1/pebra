"""Tests for pebra.core.learning_eval — calibration/eval primitives for the promotion gate (§14.4.1).

learning_eval ADDS: ece, false_proceed_rate, false_block_rate_c0_c2, lift, compute_promotion_metrics.
It REUSES brier/log-loss from prediction_error (no duplication). Convention: y=1 == harmful.
Pure stdlib; deterministic.
"""

from __future__ import annotations

import math

import pytest

from pebra.core import learning_eval as le


# --- ECE -------------------------------------------------------------------------------------

def test_ece_perfect_calibration_is_zero():
    # p == y in the edge bins: each occupied bin has conf == acc -> ECE 0.
    pairs = [(0.0, 0), (0.0, 0), (1.0, 1), (1.0, 1)]
    assert le.ece(pairs, n_bins=10) == pytest.approx(0.0)


def test_ece_worst_case_is_one():
    # always predict 1.0 but every outcome is 0 -> |acc - conf| = |0 - 1| = 1 in the single bin.
    pairs = [(1.0, 0), (1.0, 0), (1.0, 0)]
    assert le.ece(pairs, n_bins=10) == pytest.approx(1.0)


def test_ece_known_value():
    # Two bins occupied. Bin [0.0,0.1): p=0.05 x2, y=[0,0] -> conf .05, acc 0, gap .05, weight 2/4.
    # Bin [0.8,0.9): p=0.85 x2, y=[1,0] -> conf .85, acc .5, gap .35, weight 2/4.
    pairs = [(0.05, 0), (0.05, 0), (0.85, 1), (0.85, 0)]
    expected = (2 / 4) * 0.05 + (2 / 4) * 0.35
    assert le.ece(pairs, n_bins=10) == pytest.approx(expected)


def test_ece_p_equal_one_lands_in_last_bin():
    # p == 1.0 must not fall off the top edge; it belongs to the final bin.
    assert le.ece([(1.0, 1)], n_bins=10) == pytest.approx(0.0)


def test_ece_respects_n_bins():
    pairs = [(0.2, 0), (0.4, 1), (0.6, 0), (0.8, 1)]
    # different bin counts generally give different ECE; just assert it runs and is finite/non-neg.
    for nb in (1, 2, 5, 10):
        v = le.ece(pairs, n_bins=nb)
        assert 0.0 <= v <= 1.0


def test_ece_empty_raises():
    with pytest.raises(ValueError):
        le.ece([], n_bins=10)


def test_ece_nonpositive_bin_count_raises():
    with pytest.raises(ValueError):
        le.ece([(0.5, 1)], n_bins=0)
    with pytest.raises(ValueError):
        le.ece([(0.5, 1)], n_bins=-1)


# --- false_proceed_rate (y=1 == harmful) -----------------------------------------------------

def _oc(proceeded, harmful, stage="C2"):
    return le.DecisionOutcome(proceeded=proceeded, harmful=harmful, criticality_stage=stage)


def test_false_proceed_rate_none_when_no_harmful():
    outcomes = [_oc(True, False), _oc(False, False)]
    assert le.false_proceed_rate(outcomes) is None


def test_false_proceed_rate_all_harmful_proceeded():
    outcomes = [_oc(True, True), _oc(True, True)]
    assert le.false_proceed_rate(outcomes) == pytest.approx(1.0)


def test_false_proceed_rate_half():
    outcomes = [_oc(True, True), _oc(False, True), _oc(True, False)]
    # denominator = harmful (2); numerator = harmful & proceeded (1).
    assert le.false_proceed_rate(outcomes) == pytest.approx(0.5)


# --- false_block_rate_c0_c2 ------------------------------------------------------------------

def test_false_block_rate_counts_only_safe_low_criticality():
    outcomes = [
        _oc(False, False, "C1"),  # safe, low crit, blocked  -> false block
        _oc(True, False, "C0"),   # safe, low crit, proceeded -> not a false block
        _oc(False, False, "C4"),  # safe but HIGH crit -> excluded from denominator
        _oc(False, True, "C1"),   # harmful -> excluded (not "safe")
    ]
    # denominator = safe & C0-C2 (2); numerator = blocked among them (1).
    assert le.false_block_rate_c0_c2(outcomes) == pytest.approx(0.5)


def test_false_block_rate_none_when_no_safe_low():
    outcomes = [_oc(False, True, "C1"), _oc(False, False, "C4")]
    assert le.false_block_rate_c0_c2(outcomes) is None


# --- lift ------------------------------------------------------------------------------------

def test_lift_lower_is_better_positive_means_improvement():
    # Brier/log-loss are lower-is-better: baseline 0.30 -> learned 0.20 is +0.10 improvement.
    assert le.lift_lower_is_better(0.30, 0.20) == pytest.approx(0.10)


def test_lift_higher_is_better_positive_means_improvement():
    assert le.lift_higher_is_better(0.60, 0.70) == pytest.approx(0.10)


# --- compute_promotion_metrics ---------------------------------------------------------------

def test_compute_promotion_metrics_bundles_everything():
    pairs = [(0.9, 1), (0.2, 0), (0.8, 1), (0.1, 0)]
    outcomes = [_oc(True, True), _oc(False, False, "C1")]
    m = le.compute_promotion_metrics(pairs, outcomes, n_bins=10)
    assert m.n == 4
    # brier/log_loss must match the prediction_error primitives it reuses.
    from pebra.core.prediction_error import mean_brier, mean_log_loss
    assert m.brier == pytest.approx(mean_brier(pairs))
    assert m.log_loss == pytest.approx(mean_log_loss(pairs))
    assert m.ece == pytest.approx(le.ece(pairs, n_bins=10))
    assert m.false_proceed_rate == pytest.approx(1.0)
    assert m.false_block_rate_c0_c2 == pytest.approx(1.0)
    assert math.isfinite(m.brier)
