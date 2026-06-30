"""Phase 2 (math tier): each PEBRA core formula validated against a numpy/sklearn reference.

These differ from ``tests/oracles/`` in that they return a STRUCTURED result (pebra / reference /
abs_diff / tolerance / passed) suitable for a regenerable report — but the agreement bar is the same
machine-precision band, because PEBRA's core math is closed-form.
"""

from __future__ import annotations

import pytest

sklearn_metrics = pytest.importorskip("sklearn.metrics")
np = pytest.importorskip("numpy")

from benchmarks.math import oracle_metrics as om  # noqa: E402
from pebra.core import learning_eval as le  # noqa: E402
from pebra.core.prediction_error import mean_brier, mean_log_loss, mse  # noqa: E402

_PAIRS = [
    (0.90, 1), (0.20, 0), (0.80, 1), (0.10, 0),
    (0.55, 1), (0.45, 0), (0.99, 1), (0.01, 0), (0.70, 0), (0.30, 1), (0.60, 0),
]
_CONT_PAIRS = [(0.30, 0.25), (0.10, 0.12), (0.80, 0.77), (0.50, 0.61)]


def test_validate_brier_reports_pebra_and_reference_in_agreement():
    r = om.validate_brier(_PAIRS)
    assert r.metric == "brier"
    assert r.pebra == pytest.approx(mean_brier(_PAIRS), abs=0.0)
    assert r.reference == pytest.approx(
        sklearn_metrics.brier_score_loss([y for _, y in _PAIRS], [p for p, _ in _PAIRS],
                                         scale_by_half=True), abs=0.0
    )
    assert r.abs_diff <= r.tolerance
    assert r.passed is True


def test_validate_log_loss_reports_pebra_and_reference_in_agreement():
    r = om.validate_log_loss(_PAIRS)
    assert r.metric == "log_loss"
    assert r.pebra == pytest.approx(mean_log_loss(_PAIRS), abs=0.0)
    assert r.reference == pytest.approx(
        sklearn_metrics.log_loss([y for _, y in _PAIRS], [p for p, _ in _PAIRS], labels=[0, 1]),
        abs=0.0,
    )
    assert r.abs_diff <= r.tolerance
    assert r.passed is True


@pytest.mark.parametrize("n_bins", [5, 10, 15])
def test_validate_ece_matches_numpy_reference(n_bins):
    # parametrised like tests/oracles so a binning divergence at a non-default bin count is caught here
    # too, not only in the core oracle suite.
    r = om.validate_ece(_PAIRS, n_bins=n_bins)
    assert r.metric == "ece"
    assert r.pebra == pytest.approx(le.ece(_PAIRS, n_bins=n_bins), abs=0.0)
    assert r.abs_diff <= r.tolerance
    assert r.passed is True


def test_validate_mse_matches_sklearn_reference():
    r = om.validate_mse(_CONT_PAIRS)
    assert r.metric == "mse"
    assert r.pebra == pytest.approx(mse(_CONT_PAIRS), abs=0.0)
    assert r.reference == pytest.approx(
        sklearn_metrics.mean_squared_error([a for _, a in _CONT_PAIRS],
                                           [p for p, _ in _CONT_PAIRS]), abs=0.0
    )
    assert r.abs_diff <= r.tolerance
    assert r.passed is True


def test_failing_validation_is_reported_not_raised():
    # A deliberately wrong "reference" must surface as passed=False, not an exception: the report has to
    # be able to record a divergence rather than crash the run.
    r = om.OracleResult(metric="x", pebra=1.0, reference=2.0, tolerance=1e-9)
    assert r.abs_diff == pytest.approx(1.0)
    assert r.passed is False
