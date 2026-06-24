"""Benefit model (Architecture §5, AD-28) — pure, stdlib ``math`` only.

Receives already-collected before/after metric deltas and computes directionality, exposure
weighting, ``benefit``, and ``Var(benefit)``. It never reads files, calls git, runs radon, or
queries trackers — adapters supply the deltas inside ``BenefitDeltaEvidence``.

Phase 0: in ``projected`` mode (no concrete patch metrics) all deltas are absent/zero, maintainability
improvement earns **no** gate-driving credit, ``benefit = immediate_benefit``, and ``Var(benefit)``
widens to the cold-start/projected variance. A new abstraction is not automatically beneficial: it
earns value only when measured deltas reduce future change effort.
"""

from __future__ import annotations

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
PROJECTED_BENEFIT_VARIANCE: float = 0.0064
MEASURED_BENEFIT_VARIANCE: float = 0.0004


@dataclass(frozen=True)
class BenefitBreakdown:
    """AD-28 benefit output contract (Phase 0 subset)."""

    immediate_benefit: float
    maintainability_gain: float  # uncredited directional gain (for transparency)
    credited_maintainability_gain: float  # what actually entered ``benefit``
    benefit: float
    benefit_variance: float
    source_type: str
    component_provenance: list[dict] = field(default_factory=list)


def maintainability_gain(deltas: dict[str, float], future_change_exposure: float) -> float:
    """Exposure-weighted directional improvement.

    Each delta is converted to a signed *improvement* (positive = better) using ``METRIC_DIRECTION``,
    summed, and scaled by how much future change is exposed to the touched scope. Unknown metric keys
    are ignored (conservative: they earn no credit).
    """
    improvement = 0.0
    for metric, value in deltas.items():
        direction = METRIC_DIRECTION.get(metric)
        if direction is None:
            continue
        improvement += direction * value
    return future_change_exposure * improvement


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
    gain = maintainability_gain(deltas, future_change_exposure)
    if source_type == "projected":
        credited = 0.0
        variance = PROJECTED_BENEFIT_VARIANCE
    else:
        credited = gain
        variance = MEASURED_BENEFIT_VARIANCE
    benefit = immediate_benefit + credited
    return BenefitBreakdown(
        immediate_benefit=immediate_benefit,
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
