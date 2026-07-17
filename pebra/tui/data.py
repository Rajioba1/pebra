"""ObservatoryData — the TUI's read facade over persisted assessment history.

It holds the resolved ObservatoryContext's immutable identity, opens a short-lived read-only SqliteStore
per read session, delegates to the M1 shared query controller (so the TUI and the web dashboard never
drift), and closes in finally. It shapes no scores and re-derives no decisions — every value it returns
is exactly what the controller produced. Store-open failures surface as ObservatoryStoreUnavailable so
the screen can show a durable load error while keeping the last good snapshot.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.app import observatory_query_controller as oqc
from pebra.observatory_context import ObservatoryContext

_ASSESSMENTS_LIMIT = 100
_SERIES_LIMIT = 200


class ObservatoryStoreUnavailable(RuntimeError):
    """The assessment store could not be opened (missing/locked/corrupt db)."""


@dataclass(frozen=True)
class ObservatorySnapshot:
    """One consistent read of the ledger, produced by a single store session."""

    overview: dict[str, Any]
    assessments: list[dict[str, Any]]
    scores_series: list[dict[str, Any]]
    chain: dict[str, Any]


class ObservatoryData:
    def __init__(
        self,
        context: ObservatoryContext,
        *,
        assessments_limit: int = _ASSESSMENTS_LIMIT,
        series_limit: int = _SERIES_LIMIT,
    ) -> None:
        self._db_path = context.db_path
        self._repo_id = context.repo_id
        self._assessments_limit = assessments_limit
        self._series_limit = series_limit

    @property
    def repo_id(self) -> str:
        return self._repo_id

    def _open(self) -> SqliteStore:
        # Always read-only, regardless of how the context was resolved: the TUI never writes.
        try:
            return SqliteStore(self._db_path, read_only=True)
        except sqlite3.Error as exc:
            raise ObservatoryStoreUnavailable(str(exc)) from exc

    def refresh_snapshot(self) -> ObservatorySnapshot:
        """Overview, assessment rows, score series, and store-chain status — from ONE store session."""
        store = self._open()
        try:
            return ObservatorySnapshot(
                overview=oqc.overview(self._repo_id, port=store),
                assessments=oqc.list_assessments(self._repo_id, self._assessments_limit, 0, port=store),
                scores_series=oqc.scores_series(self._repo_id, self._series_limit, 0, port=store),
                chain=oqc.store_chain_status(port=store),
            )
        finally:
            store.close()

    def detail(self, assessment_id: str) -> dict[str, Any]:
        """Repo-scoped detail for one assessment, from its own short-lived session. Raises the
        controller's AssessmentNotFoundError for a missing or foreign-repo assessment."""
        store = self._open()
        try:
            return oqc.assessment_detail_for_repo(assessment_id, self._repo_id, port=store)
        finally:
            store.close()
