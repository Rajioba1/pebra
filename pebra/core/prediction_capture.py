"""prediction_capture (Milestone 4a) — pure: assess-time predicted values -> a prediction manifest.

This is the first-class record of WHAT PEBRA predicted for a scored action, captured at assess time
and persisted immutably alongside the assessment. It exists because the persisted ``result.scores``
(the flattened bag in ``decision_engine``) drops ``p_success`` and the projected maintainability
deltas — so computing calibration later from stored JSON would be reverse-engineering with missing
targets. The controller hands the in-flight evidence values here; the store writes the manifest.

Pure stdlib + core only. No I/O and no learned-fact application here; this is the measurement
substrate. Active decision adjustment is handled by snapshot read/apply before scoring.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

# Target type taxonomy (AD-29): risk vs benefit, binary vs continuous, kept SEPARATE downstream so
# calibration never mixes a Brier score with an MSE.
RISK_BINARY = "risk_binary"
BENEFIT_BINARY = "benefit_binary"
BENEFIT_CONTINUOUS = "benefit_continuous"
COST_CONTINUOUS = "cost_continuous"


@dataclass(frozen=True)
class PredictionTarget:
    """One predicted calibration target. ``predicted_value`` is a probability in [0,1] for the
    ``*_binary`` types and a raw continuous quantity for ``benefit_continuous``."""

    target_type: str
    target_name: str
    predicted_value: float
    action_id: str
    prediction_scope: str = "shadow"
    provenance: dict[str, Any] = field(default_factory=dict)
    # Phase-4 reframe: the structural feature payload of the edit (same for every target of one
    # action — features describe the edit context, not the calibration target). Persisted so M5 can
    # scope learned facts to real structural context. Empty when no enrichment was supplied.
    features: dict[str, Any] = field(default_factory=dict)


def summarize_prior_provenance(
    predictions: list[PredictionTarget] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the prior source summary carried by persisted prediction rows."""
    targets: dict[str, dict[str, Any]] = {}
    active_sources: set[str] = set()
    calibration_tags: set[str] = set()
    snapshot_ids: set[str] = set()
    for prediction in predictions:
        if isinstance(prediction, PredictionTarget):
            target_name = prediction.target_name
            provenance = prediction.provenance
        else:
            target_name = str(prediction.get("target_name") or "")
            provenance = prediction.get("provenance") or {}
        warm = provenance.get("warm_prior") if isinstance(provenance, dict) else None
        local = provenance.get("applied_snapshot") if isinstance(provenance, dict) else None
        sources: list[str] = []
        detail: dict[str, Any] = {}
        if isinstance(local, dict):
            sources.append("local_learned")
            active_sources.add("local_learned")
            snapshot_id = local.get("snapshot_id")
            if isinstance(snapshot_id, str) and snapshot_id:
                snapshot_ids.add(snapshot_id)
                detail["snapshot_id"] = snapshot_id
            for key in ("winning_fact_id", "applied_variance", "variance_floor", "variance_cap"):
                if local.get(key) is not None:
                    detail[key] = local[key]
        if isinstance(warm, dict):
            sources.append("shipped")
            active_sources.add("shipped")
            related_fields = {
                "p_success": ("p_success", "p_success_variance"),
                "review_cost": ("review_cost", "review_cost_variance"),
            }.get(target_name, (target_name,))
            field_sources = warm.get("field_sources") or {}
            target_tags: list[str] = []
            if isinstance(field_sources, dict):
                for field_name in related_fields:
                    field_source = field_sources.get(field_name)
                    calibration_tag = (
                        field_source.get("calibration_tag")
                        if isinstance(field_source, dict)
                        else None
                    )
                    if isinstance(calibration_tag, str) and calibration_tag:
                        target_tags.append(calibration_tag)
                        calibration_tags.add(calibration_tag)
            if not target_tags:
                calibration_tag = warm.get("calibration_tag")
                if isinstance(calibration_tag, str) and calibration_tag:
                    target_tags.append(calibration_tag)
                    calibration_tags.add(calibration_tag)
            if target_tags:
                detail["calibration_tag"] = target_tags[0]
                detail["calibration_tags"] = list(dict.fromkeys(target_tags))
            variance_field = related_fields[1] if len(related_fields) > 1 else None
            field_source = field_sources.get(variance_field, {}) if variance_field else {}
            if isinstance(field_source, dict):
                for key in ("applied_variance", "variance_floor", "variance_cap"):
                    if key not in detail and field_source.get(key) is not None:
                        detail[key] = field_source[key]
        primary = "local_learned" if "local_learned" in sources else (
            "shipped" if "shipped" in sources else "cold_start"
        )
        targets[target_name] = {"source": primary, "sources": sources or ["cold_start"], **detail}
    source = "local_learned" if "local_learned" in active_sources else (
        "shipped" if "shipped" in active_sources else "cold_start"
    )
    source_order = ("local_learned", "shipped")
    sources = [item for item in source_order if item in active_sources] or ["cold_start"]
    return {
        "source": source,
        "sources": sources,
        "calibration_tags": sorted(calibration_tags),
        "snapshot_ids": sorted(snapshot_ids),
        "targets": targets,
    }


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def build_prediction_manifest(
    *,
    p_success: float,
    events: list[dict[str, Any]],
    immediate_benefit: float,
    projected_deltas: dict[str, float],
    projected_benefit: float,
    action_id: str,
    review_cost: float | None = None,
    prediction_scope: str = "shadow",
    provenance: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    applied_snapshot_provenance: dict[str, Any] | None = None,
    warm_prior_provenance: dict[str, Any] | None = None,
) -> list[PredictionTarget]:
    """Build the immutable prediction manifest for one scored action.

    Targets captured (Milestone 4 set):
      risk_binary       — ``p_success``; ``p_event.<event>`` per evidence event
      benefit_binary    — ``immediate_benefit_realized`` (shadow proxy: immediate benefit
                           clamped to a probability-shaped value)
      benefit_continuous— ``maintainability_delta.<metric>`` per projected delta; ``measured_benefit``

    ``features`` (Phase-4 reframe) is the structural feature payload of the edit; it is attached
    (copied) to every target so M5 can scope learned facts to real structural context. When learned
    overrides were applied, the target-specific override provenance is persisted with that target so
    calibration can see both the used value and the raw pre-override value.
    """
    prov = dict(provenance or {"provider": "pebra", "source_type": "derived"})
    applied_by_target = {
        item.get("target"): item
        for item in (applied_snapshot_provenance or {}).get("applied_facts", [])
        if isinstance(item, dict)
    }
    applied_snapshot_id = (applied_snapshot_provenance or {}).get("snapshot_id")
    warm_fields = set((warm_prior_provenance or {}).get("applied_fields", []))

    def _warm_applies(target_name: str) -> bool:
        related_fields = {
            "p_success": {"p_success", "p_success_variance"},
            "review_cost": {"review_cost", "review_cost_variance"},
        }.get(target_name, {target_name})
        return bool(warm_fields.intersection(related_fields))

    def _target(
        target_type: str,
        name: str,
        value: float,
        provenance_update: dict[str, Any] | None = None,
    ) -> PredictionTarget:
        target_provenance = {**prov, **(provenance_update or {})}
        applied = applied_by_target.get(name)
        if applied is not None:
            target_provenance["applied_snapshot"] = {
                "snapshot_id": applied_snapshot_id,
                **copy.deepcopy(applied),
            }
        if _warm_applies(name):
            target_provenance["warm_prior"] = copy.deepcopy(warm_prior_provenance)
        return PredictionTarget(
            target_type=target_type,
            target_name=name,
            predicted_value=value,
            action_id=action_id,
            prediction_scope=prediction_scope,
            provenance=target_provenance,
            # deep snapshot: an immutable record, isolated from later caller mutation (JSON-safe payload)
            features=copy.deepcopy(features) if features else {},
        )

    manifest: list[PredictionTarget] = [_target(RISK_BINARY, "p_success", p_success)]
    for ev in events:
        manifest.append(_target(RISK_BINARY, f"p_event.{ev['event']}", ev["p_event"]))
    manifest.append(
        _target(
            BENEFIT_BINARY,
            "immediate_benefit_realized",
            _clamp_unit(immediate_benefit),
            {
                "source_type": "elicited_probability_proxy",
                "target_semantics": "immediate_benefit_clamped_to_probability_proxy",
            },
        )
    )
    for metric, value in projected_deltas.items():
        manifest.append(_target(BENEFIT_CONTINUOUS, f"maintainability_delta.{metric}", value))
    manifest.append(_target(BENEFIT_CONTINUOUS, "measured_benefit", projected_benefit))
    if review_cost is not None:
        manifest.append(_target(COST_CONTINUOUS, "review_cost", review_cost))
    return manifest
