"""Structured oracle validation of PEBRA's pure scoring formulas against numpy/sklearn references.

Each ``validate_*`` computes the PEBRA value (from ``pebra.core``, never re-derived) and an independent
reference value, and returns an :class:`OracleResult`. A divergence is RECORDED (``passed=False``), not
raised — the math report must be able to log a discrepancy rather than crash the run.

numpy/sklearn are benchmark-only references; ``pebra.core`` never imports them (purity contract).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn import metrics as sk

from pebra.core import learning_eval as le
from pebra.core.prediction_error import mean_brier, mean_log_loss, mse

# PEBRA's core math is closed-form (no optimizer), so agreement with the references is machine-precision,
# not a tolerance band. log-loss is looser because PEBRA clips at 1e-15 while sklearn clips at dtype eps.
_TOL_EXACT = 1e-12
_TOL_LOG_LOSS = 1e-9


@dataclass(frozen=True)
class OracleResult:
    """One formula's PEBRA-vs-reference comparison. ``abs_diff``/``passed`` are derived so a result can
    be constructed from just the two values + tolerance."""

    metric: str
    pebra: float
    reference: float
    tolerance: float

    @property
    def abs_diff(self) -> float:
        return abs(self.pebra - self.reference)

    @property
    def passed(self) -> bool:
        return self.abs_diff <= self.tolerance


def _numpy_ece(pairs: list[tuple[float, int]], n_bins: int) -> float:
    """Independent numpy reference for equal-width ECE (same convention as ``learning_eval.ece``)."""
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
        ece += (int(m.sum()) / n) * abs(float(y[m].mean()) - float(p[m].mean()))
    return float(ece)


def validate_brier(pairs: list[tuple[float, int]]) -> OracleResult:
    y_true = [y for _, y in pairs]
    y_prob = [p for p, _ in pairs]
    # scale_by_half=True selects the classic binary Brier mean((p-y)^2) PEBRA computes (sklearn's
    # default multiclass form is 2x that — the convention trap the oracle suite documents).
    reference = float(sk.brier_score_loss(y_true, y_prob, scale_by_half=True))
    return OracleResult("brier", mean_brier(pairs), reference, _TOL_EXACT)


def validate_log_loss(pairs: list[tuple[float, int]]) -> OracleResult:
    y_true = [y for _, y in pairs]
    y_prob = [p for p, _ in pairs]
    reference = float(sk.log_loss(y_true, y_prob, labels=[0, 1]))
    return OracleResult("log_loss", mean_log_loss(pairs), reference, _TOL_LOG_LOSS)


def validate_ece(pairs: list[tuple[float, int]], n_bins: int = 10) -> OracleResult:
    return OracleResult("ece", le.ece(pairs, n_bins=n_bins), _numpy_ece(pairs, n_bins), _TOL_EXACT)


def validate_mse(pairs: list[tuple[float, float]]) -> OracleResult:
    reference = float(sk.mean_squared_error([a for _, a in pairs], [p for p, _ in pairs]))
    return OracleResult("mse", mse(pairs), reference, _TOL_EXACT)
