"""destructive_op_model — pure event-injection model for file-level destructive operations.

Given a file operation kind + the file's fan-in roll-up + architecture evidence, returns the
consequence events to inject into AssessmentInput.events so the existing event-class floor in
assessment_builder (AD-1) applies automatically. Pure stdlib + core models. No I/O.

ONLY DELETE injects. RENAME/MOVE are a different failure mode (import-path migration, not symbol
loss) — they are detected upstream but scored later via the import/blast graph, NOT via call fan-in
(using fan-in for a move gives false confidence). CREATE has no callers to break.

SCALING:  p_event = baseline(arch/migration/schema) + fan_in_bonus(resolved rollup), capped.
  elicited_disutility is a conservative mid-range prior; assessment_builder floors it to the
  criticality value (C3=0.80 / C4=1.00) automatically (AD-1), so criticality drives severity.

NO-GRAPH BASELINE (the outage blind spot): when the rollup is 'unresolved' (graph absent / file too
new), fan_in_bonus is 0.0, but the baseline is still non-zero for domain_entrypoint / migration /
schema / architecture-anchor — so deleting a config/entrypoint/migration with zero known callers
still generates a dependency_break, reflecting real operational risk call-graph can't see.

DOUBLE-COUNT NOTE: the architecture penalty (assessment_builder._architecture_scope_penalty) lowers
edit_confidence (CONFIDENCE channel → Gate 8); these events raise expected_loss (RISK channel →
Gate 3). Both respond to entrypoint/god-node signals by design; no single formula uses a signal twice.

All numeric constants are prior_uncalibrated (spec §2.4) — to be calibrated against outcomes in M5.
"""

from __future__ import annotations

from typing import Any

from pebra.core.models import ArchitectureEvidence, FileFanInRollup

_P_EVENT_CAP = 0.45
_ABSOLUTE_FLOOR = 0.03            # deleting any file carries a small baseline risk
_BASELINE_ENTRYPOINT = 0.15
_BASELINE_MIGRATION_SCHEMA = 0.20
_BASELINE_GOD_NODE = 0.10
_BASELINE_ANCHOR = 0.08
_GOD_NODE_THRESHOLD = 0.75
_FANIN_BONUS_MAX = 0.25
_FANIN_ANCHOR_PCTL = 0.90         # percentile at which the fan-in bonus saturates
_BASE_DISUTILITY_DEPENDENCY_BREAK = 0.60
_BASE_DISUTILITY_PUBLIC_API_BREAK = 0.70


def _baseline_p_event(
    arch: ArchitectureEvidence, is_migration: bool, is_schema_change: bool
) -> float:
    p = _ABSOLUTE_FLOOR
    if arch.domain_entrypoint:
        p = max(p, _BASELINE_ENTRYPOINT)
    if is_migration or is_schema_change:
        p = max(p, _BASELINE_MIGRATION_SCHEMA)
    if arch.god_node_score >= _GOD_NODE_THRESHOLD:
        p = max(p, _BASELINE_GOD_NODE)
    if arch.architecture_anchor_score > 0.0:
        p = max(p, _BASELINE_ANCHOR)
    return p


def _fan_in_bonus(rollup: FileFanInRollup) -> float:
    if rollup.resolution_method == "unresolved":
        return 0.0  # no trusted graph -> no bonus (baseline still applies)
    pctl = rollup.file_symbol_fanin_rollup_percentile
    return min(_FANIN_BONUS_MAX, max(0.0, pctl) * (_FANIN_BONUS_MAX / _FANIN_ANCHOR_PCTL))


def _event(name: str, p_event: float, disutility: float) -> dict[str, Any]:
    return {
        "event": name,
        "p_event": p_event,
        "elicited_disutility": disutility,
        "probability_source_type": "prior_uncalibrated",
        "disutility_source_type": "prior_uncalibrated",
    }


def events_for_destructive_op(
    *,
    op_kind: str,
    rollup: FileFanInRollup,
    arch: ArchitectureEvidence,
    is_public_api: bool = False,
    is_migration: bool = False,
    is_schema_change: bool = False,
) -> list[dict[str, Any]]:
    """Events to inject for a destructive op. Only DELETE injects; CREATE/RENAME/MOVE return []."""
    if op_kind != "DELETE":
        return []
    p_event = min(
        _P_EVENT_CAP, _baseline_p_event(arch, is_migration, is_schema_change) + _fan_in_bonus(rollup)
    )
    events = [_event("dependency_break", p_event, _BASE_DISUTILITY_DEPENDENCY_BREAK)]
    if is_public_api:
        events.append(_event("public_api_break", p_event, _BASE_DISUTILITY_PUBLIC_API_BREAK))
    return events
