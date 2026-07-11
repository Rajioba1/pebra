"""Benefit model (Architecture §5, AD-28) — pure, stdlib ``math`` only.

Receives already-collected before/after metric deltas and computes directionality, exposure
weighting, ``benefit``, and ``Var(benefit)``. It never reads files, calls git, runs a complexity tool, or
queries trackers — adapters supply the deltas inside ``BenefitDeltaEvidence``.

In ``projected`` mode (no concrete patch metrics) all deltas are absent/zero, maintainability
improvement earns **no** gate-driving credit, ``benefit = immediate_benefit``, and ``Var(benefit)``
widens to the cold-start/projected variance. A new abstraction is not automatically beneficial: it
earns value only when measured deltas reduce future change effort.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Per-metric directionality (Architecture §5). +1 = higher delta is better; -1 = lower delta is better.
METRIC_DIRECTION: dict[str, int] = {
    # lower is better
    "complexity_delta": -1,
    "coupling_delta": -1,
    "duplication_delta": -1,
    "api_surface_delta": -1,
    "technical_debt_interest_delta": -1,
    "recurrence_delta": -1,
    # higher is better
    "maintainability_index_delta": +1,  # RCA MI (0-100); higher = more maintainable (Slice 4b)
    "testability_delta": +1,
    "cohesion_delta": +1,
    "modularity_delta": +1,
    "encapsulation_delta": +1,
    "observability_delta": +1,
    "operability_delta": +1,
    "analyzability_delta": +1,
    "modifiability_delta": +1,
    "reusability_delta": +1,
    "portability_delta": +1,
}

# Cold-start / projected variance (prior_uncalibrated, AD-5/AD-9): projected benefit is the most
# uncertain; measured benefit narrows it.
PROJECTED_BENEFIT_VARIANCE: float = 0.04
MEASURED_BENEFIT_VARIANCE: float = 0.0004

# Conservative unit-utility ceiling for a learned ``measured_benefit`` override (prior_uncalibrated).
# The continuous benefit override (unlike the [0.01,0.99] probability clamp) is otherwise raw, so a
# malformed/out-of-range observed value could inflate expected_utility→RAU without bound. Clamping to
# [0, 1] is the safe direction — it under-credits an unusually-large benefit rather than over-inflating
# RAU. Raise this only once the benefit scale is confirmed to exceed unit utility.
BENEFIT_OVERRIDE_MAX: float = 1.0


@dataclass(frozen=True)
class BenefitBreakdown:
    """AD-28 benefit output contract."""

    immediate_benefit: float
    maintainability_gain: float  # uncredited directional gain (for transparency)
    credited_maintainability_gain: float  # what actually entered ``benefit``
    benefit: float
    benefit_variance: float
    source_type: str
    component_provenance: list[dict] = field(default_factory=list)


def _unit_benefit(value: float) -> float:
    """Finite unit-utility value; malformed/unbounded caller evidence earns no extra authority."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0.0
    return max(0.0, min(BENEFIT_OVERRIDE_MAX, value))


def maintainability_gain(deltas: dict[str, float], future_change_exposure: float) -> float:
    """Exposure-weighted directional improvement.

    Tool metrics use incompatible units (for example, cyclomatic points vs. MI points), so raw deltas
    must never be added directly to unit utility. Each known delta becomes a bounded directional signal
    ``[-1, 1]`` via ``x / (1 + abs(x))``; signals are averaged, then scaled by bounded graph exposure.
    Unknown/non-finite metrics are ignored (conservative: they earn no credit).
    """
    signals: list[float] = []
    for metric, value in deltas.items():
        direction = METRIC_DIRECTION.get(metric)
        if (
            direction is None
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            continue
        signals.append(direction * value / (1.0 + abs(value)))
    if not signals:
        return 0.0
    if (
        isinstance(future_change_exposure, bool)
        or not isinstance(future_change_exposure, (int, float))
        or not math.isfinite(future_change_exposure)
    ):
        return 0.0
    exposure = max(0.0, min(1.0, future_change_exposure))
    return exposure * (sum(signals) / len(signals))


def resolve_benefit(
    immediate_benefit: float,
    deltas: dict[str, float],
    source_type: str,
    future_change_exposure: float = 0.0,
) -> BenefitBreakdown:
    """Compute ``benefit`` and ``Var(benefit)`` from the immediate benefit + maintainability deltas.

    ``source_type`` ∈ {projected, derived, measured}. Projected mode credits no maintainability gain
    (plan §5 Phase-0 stub) so ``benefit == immediate_benefit``; measured/derived mode credits the
    exposure-weighted directional gain.
    """
    bounded_immediate = _unit_benefit(immediate_benefit)
    gain = maintainability_gain(deltas, future_change_exposure)
    if source_type == "projected":
        credited = 0.0
        variance = PROJECTED_BENEFIT_VARIANCE
    else:
        credited = gain
        variance = MEASURED_BENEFIT_VARIANCE
    benefit = _unit_benefit(bounded_immediate + credited)
    return BenefitBreakdown(
        immediate_benefit=bounded_immediate,
        maintainability_gain=gain,
        credited_maintainability_gain=credited,
        benefit=benefit,
        benefit_variance=variance,
        source_type=source_type,
        component_provenance=[
            {"component": "immediate_benefit", "source_type": source_type, "provider": "pebra"},
            {"component": "maintainability_gain", "source_type": source_type, "provider": "pebra"},
        ],
    )
