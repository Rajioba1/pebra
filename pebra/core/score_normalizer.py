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

# Cold-start total utility variance (prior_uncalibrated) — widest, used only when nothing better.
COLD_START_UTILITY_VARIANCE: float = 0.04


def clamp_unit(x: float) -> float:
    """Clamp a value to [0, 1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


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
) -> tuple[dict[str, float], float, str]:
    """Return ``(variance_breakdown, total_variance, source)`` following AD-5 precedence."""
    # Precedence 1 — explicit breakdown supplied with the input.
    if explicit_breakdown:
        total = sum(explicit_breakdown.values())
        return dict(explicit_breakdown), total, "explicit"

    # Precedence 2 — first-order error propagation from component variances.
    have_components = all(
        v is not None
        for v in (benefit, p_success, var_p_success, var_benefit, var_review_cost)
    )
    if have_components:
        assert benefit is not None and p_success is not None
        assert var_p_success is not None and var_benefit is not None and var_review_cost is not None
        breakdown = {
            "p_success": (benefit**2) * var_p_success,
            "benefit": (p_success**2) * var_benefit,
            "event_losses": event_variance or 0.0,
            "review_cost": var_review_cost,
            "scenario_variance": scenario_variance or 0.0,
        }
        return breakdown, sum(breakdown.values()), "first_order"

    # Precedence 3 — cold-start default.
    breakdown = {"cold_start_total": COLD_START_UTILITY_VARIANCE}
    return breakdown, COLD_START_UTILITY_VARIANCE, "cold_start"
