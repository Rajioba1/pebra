"""Pure multi-owner aggregation for one candidate action.

The action still receives one decision. Owner detail is retained long enough to combine graph reach
without double-counting shared impacted nodes, preserve the worst-owner safety floor, and add only
bounded breadth above that floor.
"""

from __future__ import annotations

import math

from pebra.core.models import CandidateAction, CandidateAggregateEvidence, FanInEvidence
from pebra.core.patch_paths import touched_files

_BREADTH_CAP = 0.08


def _domain(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    return normalized.split("/", 1)[0] if "/" in normalized else "."


def _owner_exposure(owner) -> float:
    return max(
        0.0,
        min(
            1.0,
            max(
                owner.fan_in_percentile,
                owner.impact_percentile,
                owner.transitive_impact_percentile,
            ),
        ),
    )


def aggregate_candidate(
    action: CandidateAction, fanin: FanInEvidence | None
) -> CandidateAggregateEvidence:
    owners = tuple(sorted((fanin.owner_risk if fanin else ()), key=lambda o: o.node_id))
    resolved_files = {o.file_path.replace("\\", "/") for o in owners if o.file_path}
    files = tuple(sorted(
        {f.replace("\\", "/") for f in action.expected_files if f}
        | set(touched_files(action.proposed_patch or ""))
        | resolved_files
    ))
    languages = tuple(sorted({o.language for o in owners if o.language}))
    domains = {_domain(f) for f in files}

    impacted: set[str] = set()
    weighted_total = 0.0
    weight_total = 0.0
    for owner in owners:
        exposure = _owner_exposure(owner)
        owner_impacted = set(owner.impacted_node_ids)
        weight = float(len(owner_impacted)) if owner_impacted else 1.0
        impacted.update(owner_impacted)
        weighted_total += exposure * weight
        weight_total += weight

    max_exposure = max((_owner_exposure(o) for o in owners), default=0.0)
    # One file / one owner is identity. Additional breadth is logarithmic and capped so it can never
    # dominate the measured graph reach or dilute the worst-owner floor.
    breadth = (
        0.025 * math.log2(max(1, len(owners)))
        + 0.020 * math.log2(max(1, len(files)))
        + 0.010 * max(0, len(domains) - 1)
        + 0.010 * max(0, sum(1 for owner in owners if owner.is_public_contract) - 1)
    )
    file_count = len(files)
    resolved_count = len(resolved_files & set(files)) if files else len(resolved_files)
    aggregate_exposure = 0.0
    if fanin is not None:
        if fanin.symbol_caller_count > 0:
            aggregate_exposure = max(aggregate_exposure, fanin.symbol_fan_in_percentile)
        if fanin.modify_impact_count > 0:
            aggregate_exposure = max(aggregate_exposure, fanin.modify_impact_percentile)
        if fanin.modify_transitive_impact_count > 0:
            aggregate_exposure = max(
                aggregate_exposure, fanin.modify_transitive_impact_percentile
            )
    return CandidateAggregateEvidence(
        file_count=file_count,
        resolved_file_count=resolved_count,
        unresolved_file_count=max(0, file_count - resolved_count),
        owner_count=len(owners),
        languages=languages,
        domain_count=len(domains),
        impacted_node_count=len(impacted),
        public_contract_count=sum(1 for o in owners if o.is_public_contract),
        changed_owner_edge_count=fanin.changed_owner_edge_count if fanin else 0,
        max_owner_exposure=max_exposure,
        weighted_owner_exposure=(weighted_total / weight_total if weight_total else 0.0),
        # CodeGraph already computes these percentiles over the UNION of all changed owners. Reuse
        # that order-invariant aggregate instead of recombining per-owner percentiles a second time.
        cumulative_exposure=max(max_exposure, min(1.0, aggregate_exposure)),
        breadth_bonus=min(_BREADTH_CAP, max(0.0, breadth)),
        resolution_coverage=(resolved_count / file_count if file_count else 0.0),
    )
