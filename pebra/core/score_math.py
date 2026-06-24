"""Scoring math (Architecture §5) — pure, stdlib ``math`` only.

Every function here is a deterministic function of its inputs: no randomness, no I/O, no model
calls. This is what makes every PEBRA number reconstructable from ``core/`` alone.

Formulas (Architecture §5):
    disutility_j     = max(elicited_j, STAGE_MAP[stage])  iff event ∈ CONSEQUENCE_BEARING_EVENTS
                     = elicited_j                          otherwise              (event-class floor, AD-1)
    expected_loss    = Σ_j p_event_j · disutility_j
    expected_utility = p_success · benefit − expected_loss − review_cost
    utility_sd       = sqrt(Σ variance contribution terms)                        (first-order, §7.2)
    RAU              = expected_utility − z · utility_sd   (z = 1.28, 90% lower bound)
    edit_confidence  = exp(Σ_i w_i · ln(x_i))  over 6 factors, w_i = 1/6          (weighted geo. mean)
    risk_budget_used = expected_loss / effective_threshold
    blast_score      = direct + 0.5 · transitive   (normalized to [0,1])
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pebra.core.constants import (
    CONSEQUENCE_BEARING_EVENTS,
    EDIT_CONFIDENCE_FACTORS,
    EDIT_CONFIDENCE_WEIGHT,
    Z_ALPHA_90,
)


def apply_criticality_floor(
    event: str, elicited_disutility: float, criticality_value: float
) -> tuple[float, bool]:
    """Event-class-aware disutility floor (AD-1).

    The criticality floor applies *only* to consequence-bearing events. For any other event the
    elicited disutility is used unchanged — criticality never silently inflates ordinary events.
    Returns ``(disutility, floor_applied)``.
    """
    if event in CONSEQUENCE_BEARING_EVENTS:
        if criticality_value > elicited_disutility:
            return criticality_value, True
        return elicited_disutility, False
    return elicited_disutility, False


def expected_loss(events: Sequence[Mapping[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """expected_loss = Σ_j p_event_j · disutility_j.

    ``events`` rows carry ``p_event`` and a post-floor ``disutility``. Returns the total plus a
    per-event component list (each with its own ``expected_loss`` contribution) for explanation.
    """
    components: list[dict[str, Any]] = []
    total = 0.0
    for ev in events:
        contribution = ev["p_event"] * ev["disutility"]
        total += contribution
        components.append(
            {
                "event": ev.get("event"),
                "p_event": ev["p_event"],
                "disutility": ev["disutility"],
                "expected_loss": contribution,
            }
        )
    return total, components


def expected_utility(
    p_success: float, benefit: float, expected_loss: float, review_cost: float
) -> float:
    """expected_utility = p_success · benefit − expected_loss − review_cost (§7.1)."""
    return p_success * benefit - expected_loss - review_cost


def net_benefit_score(benefit: float, expected_loss: float, review_cost: float) -> float:
    """net_benefit_score = benefit − expected_loss − review_cost (§12.14, alternative ranking lens)."""
    return benefit - expected_loss - review_cost


def utility_sd(variance_terms: Mapping[str, float] | Iterable[float]) -> float:
    """utility_sd = sqrt(Σ variance contribution terms) (first-order error propagation, §7.2).

    Accepts either the named variance breakdown (mapping) or a bare iterable of contribution terms.
    """
    if isinstance(variance_terms, Mapping):
        values = variance_terms.values()
    else:
        values = variance_terms
    total = math.fsum(values)
    if total < 0:
        raise ValueError("variance must be non-negative")
    return math.sqrt(total)


def risk_adjusted_utility(
    expected_utility: float, utility_sd: float, z: float = Z_ALPHA_90
) -> float:
    """RAU = expected_utility − z · utility_sd (AD-2; z = 1.28 ⇒ 90% lower bound)."""
    return expected_utility - z * utility_sd


def edit_confidence(factors: Mapping[str, float]) -> float:
    """edit_confidence = weighted geometric mean of the six factors, w = 1/6 each (§7.4).

    Implemented as exp(Σ w_i · ln(x_i)) so confident-low factors pull the mean down multiplicatively
    rather than being averaged away. Requires every canonical factor to be present and in (0, 1].
    """
    missing = [f for f in EDIT_CONFIDENCE_FACTORS if f not in factors]
    if missing:
        raise ValueError(f"edit_confidence missing factors: {missing}")
    log_sum = 0.0
    for f in EDIT_CONFIDENCE_FACTORS:
        x = factors[f]
        if not (0.0 < x <= 1.0):
            raise ValueError(f"edit_confidence factor {f}={x} out of (0, 1]")
        log_sum += EDIT_CONFIDENCE_WEIGHT * math.log(x)
    return math.exp(log_sum)


def risk_budget_used(expected_loss: float, effective_threshold: float) -> float:
    """risk_budget_used = expected_loss / effective_threshold (ratio; > 1.0 ⇒ over budget)."""
    if effective_threshold <= 0:
        raise ValueError("effective_threshold must be positive")
    return expected_loss / effective_threshold


def blast_score(direct: float, transitive: float, scale: float = 1.0) -> float:
    """blast_score = direct + 0.5 · transitive, squashed monotonically into [0, 1].

    The raw reach (codeindex impact.py: ``d + 0.5·t``) is normalized with a saturating transform
    ``raw / (raw + scale)`` so the score stays in [0, 1] and grows monotonically with reach without
    needing the total graph size. Tagged ``source_type=estimated`` by callers until calibrated.
    """
    raw = direct + 0.5 * transitive
    if raw < 0:
        raise ValueError("blast counts must be non-negative")
    return raw / (raw + scale)
