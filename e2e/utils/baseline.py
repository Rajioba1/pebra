"""Baseline comparison — tolerance-based diff of an assess payload against a frozen baseline.

Decision is compared exactly; score fields within an absolute tolerance; volatile identity fields
(assessment_id / repo_id / guidance_packet_id) are excluded because they are not stable across runs.
Pure stdlib; unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Score fields PEBRA emits in the assess payload (composition.assess_payload). Compared with tolerance.
_SCORE_FIELDS = (
    "rau", "edit_confidence", "expected_loss", "expected_utility",
    "utility_sd", "risk_budget_used", "benefit",
)


@dataclass
class BaselineResult:
    passed: bool
    diffs: list[str] = field(default_factory=list)


def compare_payload(actual: dict, baseline: dict, *, tolerance: float = 0.02) -> BaselineResult:
    """Compare an assess payload to a baseline. ``recommended_decision`` must match exactly; each score
    in ``_SCORE_FIELDS`` must be within ``tolerance`` (absolute). Missing-on-both is fine; volatile id
    fields are never compared."""
    diffs: list[str] = []

    a_dec = actual.get("recommended_decision")
    b_dec = baseline.get("recommended_decision")
    if a_dec != b_dec:
        diffs.append(f"recommended_decision: {a_dec!r} != baseline {b_dec!r}")

    a_scores = actual.get("scores", {}) or {}
    b_scores = baseline.get("scores", {}) or {}
    for field_name in _SCORE_FIELDS:
        if field_name not in a_scores and field_name not in b_scores:
            continue
        a_val = a_scores.get(field_name)
        b_val = b_scores.get(field_name)
        if a_val is None or b_val is None:
            diffs.append(f"scores.{field_name}: {a_val!r} vs baseline {b_val!r} (one missing)")
            continue
        if abs(float(a_val) - float(b_val)) > tolerance:
            diffs.append(
                f"scores.{field_name}: {a_val} vs baseline {b_val} (|Δ| > {tolerance})"
            )

    return BaselineResult(passed=not diffs, diffs=diffs)
