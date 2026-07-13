"""ScoreNormalizer (Architecture §5, AD-5) — pure, stdlib only.

Maps raw evidence metrics to the score ranges score_math expects, and resolves the utility-variance
**precedence** (AD-5):

  1. explicit variance breakdown supplied with the input  (precedence 1 — used by the §10 example)
  2. first-order error propagation from component variances (§7.2)
  3. cold-start default variance                            (prior_uncalibrated)

It is deterministic, monotonic for declared directions, and provenance-preserving. It never calls
tools, reads files, or infers new evidence.
"""

from __future__ import annotations

from pebra.core.constants import COLD_START_VARIANCES

# Cold-start total utility variance (prior_uncalibrated) — widest, used only when nothing better.
COLD_START_UTILITY_VARIANCE: float = sum(COLD_START_VARIANCES.values())


def clamp_unit(x: float) -> float:
    """Clamp a value to [0, 1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def variance_from_confidence(confidence: float) -> float:
    """AD-5 confidence-derived variance fallback: Var(x) = ((1 - confidence) / 2)^2."""
    c = clamp_unit(confidence)
    return ((1.0 - c) / 2.0) ** 2


def _component_variance(
    supplied: float | None, confidence: float | None, cold_start_key: str
) -> float:
    if supplied is not None and supplied >= 0.0:
        return supplied
    if confidence is not None:
        return variance_from_confidence(confidence)
    return COLD_START_VARIANCES[cold_start_key]


def resolve_utility_variance(
    explicit_breakdown: dict[str, float] | None = None,
    *,
    benefit: float | None = None,
    p_success: float | None = None,
    var_p_success: float | None = None,
    var_benefit: float | None = None,
    var_review_cost: float | None = None,
    event_variance: float | None = None,
    scenario_variance: float | None = None,
    p_success_confidence: float | None = None,
    benefit_confidence: float | None = None,
    review_cost_confidence: float | None = None,
    event_confidence: float | None = None,
    disutility_confidence: float | None = None,
) -> tuple[dict[str, float], float, str]:
    """Return ``(variance_breakdown, total_variance, source)`` following AD-5 precedence."""
    # Precedence 1 — explicit breakdown supplied with the input.
    if explicit_breakdown:
        total = sum(explicit_breakdown.values())
        return dict(explicit_breakdown), total, "explicit"

    # Precedence 2 — first-order error propagation from component variances.
    have_components = benefit is not None and p_success is not None
    if have_components:
        assert benefit is not None and p_success is not None
        p_success_var = _component_variance(
            var_p_success, p_success_confidence, "p_success"
        )
        benefit_var = _component_variance(var_benefit, benefit_confidence, "benefit")
        review_cost_var = _component_variance(
            var_review_cost, review_cost_confidence, "review_cost"
        )
        if event_variance is None:
            event_variance = _component_variance(None, event_confidence, "p_event")
            event_variance += _component_variance(None, disutility_confidence, "disutility")
        breakdown = {
            "p_success": (benefit**2) * p_success_var,
            "benefit": (p_success**2) * benefit_var,
            "event_losses": event_variance,
            "review_cost": review_cost_var,
            "scenario_variance": (
                scenario_variance
                if scenario_variance is not None
                else COLD_START_VARIANCES["scenario_variance"]
            ),
        }
        return breakdown, sum(breakdown.values()), "first_order"

    # Precedence 3 — cold-start default.
    breakdown = {
        "p_success": COLD_START_VARIANCES["p_success"],
        "benefit": COLD_START_VARIANCES["benefit"],
        "event_losses": (
            COLD_START_VARIANCES["p_event"] + COLD_START_VARIANCES["disutility"]
        ),
        "review_cost": COLD_START_VARIANCES["review_cost"],
        "scenario_variance": COLD_START_VARIANCES["scenario_variance"],
    }
    return breakdown, COLD_START_UTILITY_VARIANCE, "cold_start"
