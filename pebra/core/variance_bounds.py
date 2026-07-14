"""Shared safety bounds for learned predictive variance."""

from __future__ import annotations

import math
from dataclasses import dataclass

from pebra.core.constants import COLD_START_VARIANCES, LEARNED_VARIANCE_FLOOR_RATIO


@dataclass(frozen=True)
class BoundedVariance:
    epistemic_variance: float
    aleatoric_variance: float | None
    applied_variance: float
    variance_floor: float
    variance_cap: float


def bound_predictive_variance(
    component: str,
    epistemic_variance: float,
    aleatoric_variance: float | None,
) -> BoundedVariance:
    """Combine uncertainty components without allowing learned caution to vanish or explode.

    Missing aleatoric evidence is treated as legacy/incomplete calibration and therefore retains the
    cold-start cap. Both supplied components must be finite and nonnegative.
    """
    epistemic = float(epistemic_variance)
    aleatoric = None if aleatoric_variance is None else float(aleatoric_variance)
    if not math.isfinite(epistemic) or epistemic < 0.0:
        raise ValueError("epistemic_variance must be finite and nonnegative")
    if aleatoric is not None and (not math.isfinite(aleatoric) or aleatoric < 0.0):
        raise ValueError("aleatoric_variance must be finite and nonnegative")
    cap = COLD_START_VARIANCES[component]
    floor = cap * LEARNED_VARIANCE_FLOOR_RATIO
    applied = cap if aleatoric is None else max(floor, min(cap, epistemic + aleatoric))
    return BoundedVariance(
        epistemic_variance=epistemic,
        aleatoric_variance=aleatoric,
        applied_variance=applied,
        variance_floor=floor,
        variance_cap=cap,
    )
