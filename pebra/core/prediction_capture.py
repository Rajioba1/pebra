"""prediction_capture (Milestone 4a) — pure: assess-time predicted values -> a prediction manifest.

This is the first-class record of WHAT PEBRA predicted for a scored action, captured at assess time
and persisted immutably alongside the assessment. It exists because the persisted ``result.scores``
(the flattened bag in ``decision_engine``) drops ``p_success`` and the projected maintainability
deltas — so computing calibration later from stored JSON would be reverse-engineering with missing
targets. The controller hands the in-flight evidence values here; the store writes the manifest.

Pure stdlib + core only. No I/O, no learning reapplied — this is measurement substrate (Milestone 4),
not decision adjustment (Milestone 5).
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
    prediction_scope: str = "shadow",
    provenance: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    applied_snapshot_provenance: dict[str, Any] | None = None,
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
    return manifest
