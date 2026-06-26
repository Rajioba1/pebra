"""learning_store (Milestone 4d) — LearningPort impl over the hash-chained SqliteStore.

Thin adapter: it owns no math (that's ``core/prediction_error``) and no orchestration (that's
``app/learning_controller``). It just appends the computed shadow rows to the store's chains. Never
imported by ``assess_controller`` (Hard Rule — shadow measurement does not feed decisions).
"""

from __future__ import annotations

from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.ports.learning_port import LearningPort


class LearningStore(LearningPort):
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def write_measurement(
        self,
        assessment_id: str,
        rows: list[dict[str, Any]],
        repo_id: str,
        metrics: dict[str, Any],
        status: str = "shadow",
    ) -> tuple[list[str], str]:
        return self._store.insert_learning_measurement(assessment_id, rows, repo_id, metrics, status)

    def write_prediction_errors(
        self, assessment_id: str, rows: list[dict[str, Any]]
    ) -> list[str]:
        return [self._store.insert_prediction_error(assessment_id, row) for row in rows]

    def write_risk_snapshot(
        self, repo_id: str, metrics: dict[str, Any], status: str = "shadow"
    ) -> str:
        return self._store.insert_risk_snapshot(repo_id, metrics, status)
