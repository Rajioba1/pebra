"""Oracle tests: validate PEBRA's pure-stdlib scoring math against reference implementations.

These run ONLY in the dev/test env (sklearn/scipy/numpy installed). They are the ground-truth that
PEBRA's hand-rolled core math equals the established references to machine precision. The references
are NEVER imported by pebra.core (purity contract) — they live here purely as test oracles.

Because PEBRA's core math is closed-form (no optimizer), agreement is to ~1e-12, not a tolerance band.
"""

from __future__ import annotations

import pytest

sklearn_metrics = pytest.importorskip("sklearn.metrics")
np = pytest.importorskip("numpy")

from pebra.core import learning_eval as le
from pebra.core.prediction_error import mean_brier, mean_log_loss

_PAIRS = [
    (0.90, 1), (0.20, 0), (0.80, 1), (0.10, 0),
    (0.55, 1), (0.45, 0), (0.99, 1), (0.01, 0), (0.70, 0), (0.30, 1), (0.60, 0),
]


def test_mean_brier_matches_sklearn():
    y_prob = [p for p, _ in _PAIRS]
    y_true = [y for _, y in _PAIRS]
    # CONVENTION TRAP (caught by this oracle): sklearn's scale_by_half=False returns the
    # multiclass-consistent 2*(p-y)^2; the classic binary Brier mean((p-y)^2) that PEBRA computes is
    # the HALF-scaled form (scale_by_half=True, which 'auto' applies for binary targets).
    oracle = sklearn_metrics.brier_score_loss(y_true, y_prob, scale_by_half=True)
    assert mean_brier(_PAIRS) == pytest.approx(oracle, abs=1e-12)


def test_mean_log_loss_matches_sklearn():
    y_prob = [p for p, _ in _PAIRS]
    y_true = [y for _, y in _PAIRS]
    # sklearn clips at dtype eps (~2.2e-16); PEBRA clips at 1e-15. No p is at the boundary here,
    # so the two agree to well within 1e-9.
    oracle = sklearn_metrics.log_loss(y_true, y_prob, labels=[0, 1])
    assert mean_log_loss(_PAIRS) == pytest.approx(oracle, abs=1e-9)


def _numpy_ece(pairs, n_bins):
    """Independent numpy reference for equal-width ECE (same convention as learning_eval.ece)."""
    p = np.array([pp for pp, _ in pairs], dtype=float)
    y = np.array([yy for _, yy in pairs], dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    n = len(pairs)
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        if not m.any():
            continue
        ece += (m.sum() / n) * abs(y[m].mean() - p[m].mean())
    return ece


def test_ece_matches_numpy_reference():
    for nb in (5, 10, 15):
        assert le.ece(_PAIRS, n_bins=nb) == pytest.approx(_numpy_ece(_PAIRS, nb), abs=1e-12)
