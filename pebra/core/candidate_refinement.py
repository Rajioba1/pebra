"""Pure scoped graph-risk updates and deterministic refinement ranking."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any

from pebra.core.models import CandidateGraphRiskEvidence


# A complete, high-confidence continuity proof retains 35% of the prior event probability.
# It never removes the event: the independent and absolute floors below preserve uncertainty
# for unknown external consumers and behavior that structural evidence cannot establish.
STRUCTURAL_CONTINUITY_MULTIPLIER = 0.35
STRUCTURAL_CONTINUITY_PROBABILITY_FLOOR = 0.05
STRUCTURAL_CONTINUITY_MIN_CONFIDENCE = 0.90
_ALLOWED_FACT_SCOPES = {
    "exported_binding_continuity": {("public_api_break", "graph_modify_risk")},
}
# Adapter logic is language-neutral; autonomous credit is enabled only for extractor families whose
# real binary edge/signature semantics have been measured end-to-end.
MEASURED_CONTINUITY_LANGUAGES = frozenset({"typescript", "tsx"})


def apply_scoped_adjustments(
    events: list[dict[str, Any]],
    evidence: CandidateGraphRiskEvidence,
    *,
    patch_hash: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply each exact-scope structural fact at most once; malformed evidence is identity."""
    if (
        evidence.status != "available"
        or not patch_hash
        or evidence.verified_patch_hash != patch_hash
    ):
        return list(events), []

    fact_by_key: dict[tuple[str, str, tuple[str, ...]], Any] = {}
    duplicate_keys: set[tuple[str, str, tuple[str, ...]]] = set()
    for fact in evidence.facts:
        owners = tuple(sorted(set(fact.owner_node_ids)))
        key = (fact.event, fact.risk_source, owners)
        if key in fact_by_key:
            duplicate_keys.add(key)
        fact_by_key[key] = fact

    event_keys = [
        (
            str(event.get("event", "")),
            str(event.get("risk_source", "")),
            tuple(sorted(set(
                str(value) for value in event.get("owner_node_ids", ()) if value
            ))),
        )
        for event in events
    ]
    event_key_counts = Counter(event_keys)

    adjusted: list[dict[str, Any]] = []
    applied: list[str] = []
    for event in events:
        owners = tuple(sorted(set(str(value) for value in event.get("owner_node_ids", ()) if value)))
        key = (str(event.get("event", "")), str(event.get("risk_source", "")), owners)
        fact = fact_by_key.get(key)
        if (
            not owners
            or event_key_counts[key] != 1
            or key in duplicate_keys
            or fact is None
            or key[:2] not in _ALLOWED_FACT_SCOPES.get(fact.fact_kind, set())
            or not math.isfinite(fact.confidence)
            or fact.confidence < STRUCTURAL_CONTINUITY_MIN_CONFIDENCE
            or fact.confidence > 1.0
        ):
            adjusted.append(event)
            continue
        original = float(event.get("p_event", 0.0))
        multiplier = 1.0 - (
            (1.0 - STRUCTURAL_CONTINUITY_MULTIPLIER) * float(fact.confidence)
        )
        revised = min(
            original,
            max(
                STRUCTURAL_CONTINUITY_PROBABILITY_FLOOR,
                float(event.get("independent_probability_floor", 0.0)),
                original * multiplier,
            ),
        )
        updated = dict(event)
        updated["p_event"] = revised
        updated["graph_risk_update"] = {
            "fact_kind": fact.fact_kind,
            "provider": evidence.provider,
            "event": fact.event,
            "risk_source": fact.risk_source,
            "fact_confidence": fact.confidence,
            "original_probability": original,
            "revised_probability": revised,
            "probability_multiplier": multiplier,
            "probability_floor": STRUCTURAL_CONTINUITY_PROBABILITY_FLOOR,
            "calibration": "prior_uncalibrated_conservative",
            "owner_node_ids": list(owners),
        }
        adjusted.append(updated)
        applied.append(key[0])
    return adjusted, applied


@dataclass(frozen=True)
class CandidateRankInput:
    action_id: str
    eligible: bool
    needs_refinement: bool
    benefit: float
    expected_loss: float
    rau: float
    cumulative_exposure: float
    file_count: int
    owner_count: int
    domain_count: int
    resolution_coverage: float
    patch_hash: str


def rank_candidates(candidates: list[CandidateRankInput]) -> list[CandidateRankInput]:
    eligible = [candidate for candidate in candidates if candidate.eligible and candidate.needs_refinement]
    return sorted(
        eligible,
        key=lambda candidate: (
            -candidate.rau,
            candidate.expected_loss,
            -candidate.benefit,
            candidate.cumulative_exposure,
            candidate.file_count,
            candidate.owner_count,
            candidate.domain_count,
            -candidate.resolution_coverage,
            candidate.patch_hash,
            candidate.action_id,
        ),
    )
