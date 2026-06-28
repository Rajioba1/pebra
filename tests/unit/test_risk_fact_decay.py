"""Tests for pebra.core.risk_fact_decay (AD-17 / §12.7) - pure churn-driven fact decay.

effective_weight = base_weight * exp(-scope_change_count / decay_strength). Decay is driven by
scope CHURN (changes since the fact was learned), not wall-clock time (spec AD-17). Pure stdlib;
deterministic.
"""

from __future__ import annotations

import math

import pytest

from pebra.core import risk_fact_decay as rfd


def test_zero_churn_is_no_decay():
    # exp(0) == 1 -> a freshly-learned fact keeps its full base weight.
    assert rfd.effective_weight(1.0, 0) == pytest.approx(1.0)
    assert rfd.effective_weight(0.5, 0) == pytest.approx(0.5)


def test_exact_one_decay_constant():
    # count == decay_strength -> exp(-1).
    assert rfd.effective_weight(1.0, 20, decay_strength=20.0) == pytest.approx(math.exp(-1.0))


def test_decay_is_monotonically_decreasing_in_churn():
    weights = [rfd.effective_weight(1.0, c, decay_strength=20.0) for c in range(0, 60, 5)]
    assert all(later <= earlier for earlier, later in zip(weights, weights[1:]))


def test_base_weight_scales_linearly():
    # halving base halves the decayed weight (same exponential factor).
    factor = math.exp(-10 / 20.0)
    assert rfd.effective_weight(1.0, 10, decay_strength=20.0) == pytest.approx(factor)
    assert rfd.effective_weight(0.5, 10, decay_strength=20.0) == pytest.approx(0.5 * factor)


def test_large_churn_decays_below_auto_apply_threshold():
    # exp(-200/20) = exp(-10) ~= 4.5e-5. It must not be clamped upward; the
    # promotion/read side should stop auto-applying it instead.
    weight = rfd.effective_weight(1.0, 200, decay_strength=20.0)
    assert weight == pytest.approx(math.exp(-10.0))
    assert weight < rfd.MIN_EFFECTIVE_WEIGHT
    assert rfd.should_auto_apply(weight) is False


def test_weak_base_weight_is_not_boosted_by_threshold():
    weight = rfd.effective_weight(0.05, 0, decay_strength=20.0)
    assert weight == pytest.approx(0.05)
    assert rfd.should_auto_apply(weight) is False


def test_weight_at_threshold_auto_applies():
    assert rfd.should_auto_apply(rfd.MIN_EFFECTIVE_WEIGHT) is True


def test_floor_value_is_one_tenth_and_strength_twenty():
    assert rfd.MIN_EFFECTIVE_WEIGHT == pytest.approx(0.10)
    assert rfd.DEFAULT_DECAY_STRENGTH == pytest.approx(20.0)


def test_default_decay_strength_used_when_omitted():
    assert rfd.effective_weight(1.0, 20) == pytest.approx(
        rfd.effective_weight(1.0, 20, decay_strength=rfd.DEFAULT_DECAY_STRENGTH)
    )


def test_custom_decay_strength_decays_slower():
    # a larger decay_strength means slower decay -> higher weight at the same churn.
    slow = rfd.effective_weight(1.0, 20, decay_strength=100.0)
    fast = rfd.effective_weight(1.0, 20, decay_strength=10.0)
    assert slow > fast


def test_negative_churn_rejected():
    with pytest.raises(ValueError):
        rfd.effective_weight(1.0, -1)


def test_negative_base_weight_rejected():
    with pytest.raises(ValueError):
        rfd.effective_weight(-0.1, 0)


def test_nonpositive_decay_strength_rejected():
    with pytest.raises(ValueError):
        rfd.effective_weight(1.0, 5, decay_strength=0.0)
    with pytest.raises(ValueError):
        rfd.effective_weight(1.0, 5, decay_strength=-3.0)


def test_invalid_auto_apply_threshold_inputs_rejected():
    with pytest.raises(ValueError):
        rfd.should_auto_apply(-0.1)
    with pytest.raises(ValueError):
        rfd.should_auto_apply(0.5, min_effective_weight=-0.01)


def test_result_is_deterministic():
    a = rfd.effective_weight(0.8, 13, decay_strength=20.0)
    b = rfd.effective_weight(0.8, 13, decay_strength=20.0)
    assert a == b
