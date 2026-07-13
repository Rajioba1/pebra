"""snapshot_read_store (M5c) — read-only adapter: active learned facts -> SnapshotBundle.

Decodes the raw rows from SqliteStore.read_active_snapshot_rows into core SnapshotFact/SnapshotBundle,
pulling value/weight/sample_size/calibration_method out of fact_json. It is the PRIMARY applicability
gate: it drops facts below MIN_CALIBRATION_SAMPLES, with no calibration_method, or with a malformed/
non-finite value (apply_snapshot keeps a looser defense-in-depth gate). Never writes.

It is deliberately NOT named/placed in learning_store/calibration_store, so the assess path may use it
via SnapshotReadPort without breaching the assess-no-learning import-linter contract.
"""

from __future__ import annotations

import json
import math
from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact
from pebra.core.constants import MIN_CALIBRATION_SAMPLES
from pebra.ports.snapshot_read_port import SnapshotReadPort


class SnapshotReadStore(SnapshotReadPort):
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def load_active_snapshot(self, repo_id: str) -> SnapshotBundle | None:
        if not self._store.validate_learning_chains():
            return None
        raw = self._store.read_active_snapshot_rows(repo_id)
        if raw is None:
            return None
        facts: list[SnapshotFact] = []
        for row in raw["facts"]:
            fact = self._build_fact(row)
            if fact is not None:
                facts.append(fact)
        return SnapshotBundle(snapshot_id=raw["snapshot_id"], facts=tuple(facts))

    @staticmethod
    def _build_fact(row: dict[str, Any]) -> SnapshotFact | None:
        """Decode one raw row into a SnapshotFact, or None if it is malformed or fails the read-port
        calibration gate (min sample size + a calibration method + a finite value)."""
        try:
            fact = json.loads(row["fact_json"] or "{}")
            if not isinstance(fact, dict) or "value" not in fact:
                return None
            value = float(fact["value"])
            sample_size = int(fact.get("sample_size", 0))
            method = str(fact.get("calibration_method", "")).strip()
            weight = float(fact.get("weight", 1.0))
            calibration_quality = float(fact.get("calibration_quality", 1.0))
            scope_change_count = int(fact.get("scope_change_count", 0))
            variance_raw = fact.get("variance")
            variance = None if variance_raw is None else float(variance_raw)
            aleatoric_raw = fact.get("aleatoric_variance")
            aleatoric_variance = None if aleatoric_raw is None else float(aleatoric_raw)
            if sample_size < MIN_CALIBRATION_SAMPLES or not method or not math.isfinite(value):
                return None
            if (
                weight < 0.0
                or calibration_quality < 0.0
                or scope_change_count < 0
                or not math.isfinite(weight)
                or not math.isfinite(calibration_quality)
                or (variance is not None and (variance < 0.0 or not math.isfinite(variance)))
                or (
                    aleatoric_variance is not None
                    and (aleatoric_variance < 0.0 or not math.isfinite(aleatoric_variance))
                )
            ):
                return None
            scope_json = json.loads(row["scope_json"] or "{}")
            return SnapshotFact(
                fact_id=row["fact_id"],
                target_type=row["target_type"],
                target_name=row["target_name"],
                scope_kind=row["scope_kind"],
                scope_value=row["scope_value"],
                specificity_rank=int(row["specificity_rank"]),
                value=value,
                sample_size=sample_size,
                calibration_method=method,
                created_at=row["created_at"] or "",
                weight=weight,
                requires_human_ratification=False,  # SQL already filtered ratification=0
                scope_json=scope_json if isinstance(scope_json, dict) else {},
                calibration_quality=calibration_quality,
                scope_change_count=scope_change_count,
                variance=variance,
                aleatoric_variance=aleatoric_variance,
            )
        except (ValueError, TypeError):
            return None  # malformed JSON / non-numeric value -> skip, never raise
