"""Structured oracle validation of PEBRA's pure scoring formulas against numpy/sklearn references.

Each ``validate_*`` computes the PEBRA value (from ``pebra.core``, never re-derived) and an independent
reference value, and returns an :class:`OracleResult`. A divergence is RECORDED (``passed=False``), not
raised — the math report must be able to log a discrepancy rather than crash the run.

numpy/sklearn are benchmark-only references; ``pebra.core`` never imports them (purity contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.math import reference_metrics as ref
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
    """Compatibility wrapper around the independent reference lane."""
    return ref.numpy_ece(pairs, n_bins=n_bins)


def validate_brier(pairs: list[tuple[float, int]]) -> OracleResult:
    # scale_by_half=True selects the classic binary Brier mean((p-y)^2) PEBRA computes (sklearn's
    # default multiclass form is 2x that — the convention trap the oracle suite documents).
    reference = ref.sklearn_brier(pairs)
    return OracleResult("brier", mean_brier(pairs), reference, _TOL_EXACT)


def validate_log_loss(pairs: list[tuple[float, int]]) -> OracleResult:
    reference = ref.sklearn_log_loss(pairs)
    return OracleResult("log_loss", mean_log_loss(pairs), reference, _TOL_LOG_LOSS)


def validate_ece(pairs: list[tuple[float, int]], n_bins: int = 10) -> OracleResult:
    return OracleResult("ece", le.ece(pairs, n_bins=n_bins), _numpy_ece(pairs, n_bins), _TOL_EXACT)


def validate_mse(pairs: list[tuple[float, float]]) -> OracleResult:
    # pairs are (predicted, actual); sklearn's signature is (y_true, y_pred) -> actuals first. The order
    # looks reversed at the call site on purpose (MSE is symmetric, so it is also harmless either way).
    reference = ref.sklearn_mse(pairs)
    return OracleResult("mse", mse(pairs), reference, _TOL_EXACT)
