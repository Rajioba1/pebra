"""Pure cold-start prior resolution from reviewed calibration cells.

Cells are shipped as code, not loaded from disk at runtime. Active repository snapshots still run
after this resolver and therefore remain the more local learned evidence source.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from pebra.core.language_capability import classify_tier
from pebra.core.models import AssessmentInput


@dataclass(frozen=True)
class CalibratedPriorCell:
    calibration_tag: str
    sample_size: int
    action_type: str | None = None
    language_tier: str | None = None
    graph_fact_kind: str | None = None
    p_success: float | None = None
    p_success_variance: float | None = None
    review_cost: float | None = None
    review_cost_variance: float | None = None


def _valid(cell: CalibratedPriorCell) -> bool:
    values = (
        cell.p_success, cell.p_success_variance, cell.review_cost, cell.review_cost_variance,
    )
    if (
        cell.sample_size <= 0
        or not cell.calibration_tag
        or not any((cell.action_type, cell.language_tier, cell.graph_fact_kind))
    ):
        return False
    if any(value is not None and not math.isfinite(value) for value in values):
        return False
    if cell.p_success is not None and not 0.0 <= cell.p_success <= 1.0:
        return False
    if cell.review_cost is not None and cell.review_cost < 0.0:
        return False
    return not any(
        value is not None and value < 0.0
        for value in (cell.p_success_variance, cell.review_cost_variance)
    )


def _matches(cell: CalibratedPriorCell, inp: AssessmentInput) -> bool:
    if cell.action_type is not None and cell.action_type != inp.action.action_type:
        return False
    if cell.language_tier is not None and cell.language_tier != classify_tier(inp.language_capability):
        return False
    if cell.graph_fact_kind is not None:
        fact_kinds = {fact.fact_kind for fact in inp.candidate_graph_risk_evidence.facts}
        if cell.graph_fact_kind not in fact_kinds:
            return False
    return True


def _specificity(cell: CalibratedPriorCell) -> tuple[int, int, str]:
    return (
        sum(value is not None for value in (
            cell.action_type, cell.language_tier, cell.graph_fact_kind,
        )),
        cell.sample_size,
        cell.calibration_tag,
    )


def apply_warm_prior(
    inp: AssessmentInput, cells: tuple[CalibratedPriorCell, ...]
) -> AssessmentInput:
    """Resolve each missing field from its most-specific matching reviewed cell.

    Per-field resolution prevents a sparse, highly-specific cell from hiding a valid value supplied by
    a broader matching cell. Completely unscoped cells are rejected by ``_valid``.
    """
    matches = [cell for cell in cells if _valid(cell) and _matches(cell, inp)]
    if not matches:
        return inp
    explicit = inp.request.evidence
    changes: dict[str, object] = {}
    field_sources: dict[str, dict[str, object]] = {}
    selected_cells: dict[str, CalibratedPriorCell] = {}
    for field_name in (
        "p_success", "p_success_variance", "review_cost", "review_cost_variance",
    ):
        candidates = [cell for cell in matches if getattr(cell, field_name) is not None]
        if field_name in explicit or not candidates:
            continue
        cell = max(candidates, key=_specificity)
        changes[field_name] = getattr(cell, field_name)
        selected_cells[field_name] = cell
        field_sources[field_name] = {
            "calibration_tag": cell.calibration_tag,
            "sample_size": cell.sample_size,
            "action_type": cell.action_type,
            "language_tier": cell.language_tier,
            "graph_fact_kind": cell.graph_fact_kind,
        }
    if not changes:
        return inp
    primary = max(selected_cells.values(), key=_specificity)
    changes["warm_prior_provenance"] = {
        "calibration_tag": primary.calibration_tag,
        "sample_size": primary.sample_size,
        "action_type": primary.action_type,
        "language_tier": primary.language_tier,
        "graph_fact_kind": primary.graph_fact_kind,
        "applied_fields": sorted(changes),
        "field_sources": field_sources,
    }
    return dataclasses.replace(inp, **changes)
