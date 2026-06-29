"""snapshot_reconciler (Phase 5 closure) — pure drift detection between the ACTIVE learned snapshot and
the current calibration ledger.

If the active facts have diverged from what the ledger now implies (the empirical rates moved), blindly
extending the snapshot would compound stale beliefs. ``compute_drift`` measures that divergence and
``should_freeze`` is the gate the promotion flow uses to PAUSE promotion until reviewed.

Pure stdlib + core. Drift = mean over active facts of |stored value − recomputed empirical rate over the
current calibration rows matching that fact's scope|; facts whose scope matches no current row are
skipped (no fresh evidence to compare against); 0.0 when there is nothing to compare.
"""

from __future__ import annotations

import json
from typing import Any

from pebra.core.promotion_evaluator import compute_empirical_value, scope_matches_features


def _decode_active_facts(active_snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract {value, target_name, scope_kind, scope_value, scope_json} per active fact. Malformed
    fact_json/scope_json (or a missing value) is skipped, never crashes."""
    out: list[dict[str, Any]] = []
    if not active_snapshot:
        return out
    for f in active_snapshot.get("facts", []):
        try:
            raw_fact = f["fact_json"]
            fact = json.loads(raw_fact) if isinstance(raw_fact, str) else (raw_fact or {})
            value = float(fact["value"])
            raw_scope = f.get("scope_json")
            scope = json.loads(raw_scope) if isinstance(raw_scope, str) else (raw_scope or {})
        except (TypeError, ValueError, KeyError):
            continue
        out.append({
            "value": value, "target_name": f["target_name"],
            "scope_kind": f["scope_kind"], "scope_value": f["scope_value"], "scope_json": scope,
        })
    return out


def compute_drift(
    active_snapshot: dict[str, Any] | None, calibration_rows: list[dict[str, Any]]
) -> float:
    """Mean abs diff between active fact values and the recomputed empirical rate over the current rows
    matching each fact's scope. 0.0 when there are no active facts or no scope has matching rows."""
    diffs: list[float] = []
    for fact in _decode_active_facts(active_snapshot):
        matched = [
            r for r in calibration_rows
            if r.get("target_name") == fact["target_name"]
            and scope_matches_features(fact["scope_kind"], fact["scope_value"], fact["scope_json"],
                                       r.get("features") or {})
        ]
        if not matched:
            continue
        diffs.append(abs(fact["value"] - compute_empirical_value(matched)))
    if not diffs:
        return 0.0
    return sum(diffs) / len(diffs)


def should_freeze(drift: float, threshold: float) -> bool:
    """Freeze promotion when drift has reached the configured threshold."""
    return drift >= threshold
