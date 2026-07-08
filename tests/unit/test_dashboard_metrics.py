"""dashboard_metrics — pure reliability-diagram binning for the Risk Observatory calibration view.

These assert the per-bin structure the frontend charts, its fail-soft behaviour on empty input (the
dashboard never raises), and that the bins are consistent with the promotion gate's scalar ECE.
"""

from __future__ import annotations

import pytest

from pebra.core import dashboard_metrics as dm
from pebra.core import learning_eval


def test_reliability_bins_partition_and_rates() -> None:
    pairs = [(0.05, 0), (0.05, 1), (0.95, 1), (0.95, 1)]
    bins = dm.reliability_bins(pairs, n_bins=10)

    assert len(bins) == 10
    assert bins[0] == {
        "predicted_lo": pytest.approx(0.0),
        "predicted_hi": pytest.approx(0.1),
        "count": 2,
        "observed_rate": pytest.approx(0.5),
        "mean_predicted": pytest.approx(0.05),
    }
    assert bins[9]["count"] == 2
    assert bins[9]["observed_rate"] == pytest.approx(1.0)
    assert bins[9]["mean_predicted"] == pytest.approx(0.95)


def test_reliability_bins_empty_bins_have_none_rates() -> None:
    bins = dm.reliability_bins([(0.05, 1)], n_bins=10)
    assert bins[5]["count"] == 0
    assert bins[5]["observed_rate"] is None
    assert bins[5]["mean_predicted"] is None


def test_reliability_bins_empty_input_is_failsoft() -> None:
    # The dashboard must never raise on a repo with no labelled predictions yet (unlike ece()).
    bins = dm.reliability_bins([], n_bins=10)
    assert len(bins) == 10
    assert all(b["count"] == 0 and b["observed_rate"] is None for b in bins)


def test_reliability_bins_skips_malformed_rows() -> None:
    bins = dm.reliability_bins([(0.5, 1), (1.2, 1), (-0.1, 0), (float("nan"), 1), (0.8, 3)])
    assert sum(b["count"] for b in bins) == 1
    assert bins[5]["count"] == 1


def test_reliability_bins_p_equals_one_lands_in_last_bin() -> None:
    bins = dm.reliability_bins([(1.0, 1)], n_bins=10)
    assert bins[9]["count"] == 1
    assert bins[8]["count"] == 0


def test_reliability_bins_reduce_to_ece() -> None:
    # The per-bin diagram must be consistent with the promotion gate's scalar ECE: the weighted sum of
    # per-bin |observed_rate - mean_predicted| over non-empty bins equals learning_eval.ece(pairs).
    pairs = [(0.1, 0), (0.2, 1), (0.25, 0), (0.8, 1), (0.85, 1), (0.9, 0), (0.95, 1)]
    bins = dm.reliability_bins(pairs, n_bins=10)
    n = len(pairs)
    ece_from_bins = sum(
        (b["count"] / n) * abs(b["observed_rate"] - b["mean_predicted"])
        for b in bins
        if b["count"]
    )
    assert ece_from_bins == pytest.approx(learning_eval.ece(pairs))


def test_reliability_bins_rejects_nonpositive_bins() -> None:
    with pytest.raises(ValueError):
        dm.reliability_bins([(0.5, 1)], n_bins=0)
