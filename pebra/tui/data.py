"""ObservatoryData — the TUI's read facade over persisted assessment history.

It holds the resolved ObservatoryContext's immutable identity, opens a short-lived read-only SqliteStore
per read session, delegates to the M1 shared query controller (so the TUI and the web dashboard never
drift), and closes in finally. It shapes no scores and re-derives no decisions — every value it returns
is exactly what the controller produced. Store-open failures surface as ObservatoryStoreUnavailable so
the screen can show a durable load error while keeping the last good snapshot.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.app import observatory_query_controller as oqc
from pebra.app import explore_controller
from pebra.core.exploration import ExplorationResult
from pebra.core.learning_context import LearningContextRecall
from pebra.observatory_context import ObservatoryContext
from pebra.ports.repository_explorer_port import RepositoryExplorer

_ASSESSMENTS_LIMIT = 100
_SERIES_LIMIT = 200


class ObservatoryStoreUnavailable(RuntimeError):
    """The assessment store could not be opened (missing/locked/corrupt db)."""


@dataclass(frozen=True)
class ObservatorySnapshot:
    """One refresh's reads of the ledger, taken through a single short-lived read-only session.

    The reads are NOT one transactional snapshot: each is a point-in-time read of the latest committed
    state, so if the engine commits an assessment mid-refresh the header count can momentarily differ
    from the rendered rows. This self-corrects on the next refresh; it is not a torn write.
    """

    overview: dict[str, Any]
    assessments: list[dict[str, Any]]
    scores_series: list[dict[str, Any]]
    chain: dict[str, Any]
    prior_facets: dict[str, dict[str, Any]] = field(default_factory=dict)
    lesson_facets: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservatoryLearningSnapshot:
    """Explicit full learning read; intentionally outside the five-second ledger refresh."""

    snapshots: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    learning_context: dict[str, Any] = field(
        default_factory=lambda: {"status": "empty", "items": []}
    )


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
        """Overview, assessment rows, score series, and store-chain status through one short-lived
        read-only session (point-in-time reads, not a single transaction — see ObservatorySnapshot)."""
        try:
            store = self._open()
            try:
                assessments = oqc.list_assessments(
                    self._repo_id, self._assessments_limit, 0, port=store
                )
                return ObservatorySnapshot(
                    overview=oqc.overview(self._repo_id, port=store),
                    assessments=assessments,
                    scores_series=oqc.scores_series(
                        self._repo_id, self._series_limit, 0, port=store
                    ),
                    chain=oqc.store_chain_status(port=store),
                    prior_facets=oqc.assessment_prior_facets(
                        self._repo_id,
                        [row["assessment_id"] for row in assessments],
                        port=store,
                    ),
                    lesson_facets={
                        item["assessment_id"]: item
                        for item in oqc.learning_context(
                            self._repo_id,
                            [row["assessment_id"] for row in assessments],
                            port=store,
                        )["items"]
                    },
                )
            finally:
                store.close()
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ObservatoryStoreUnavailable(str(exc)) from exc

    def learning_snapshot(self) -> ObservatoryLearningSnapshot:
        """Fetch whole learning tables only on an explicit screen request, never on the ledger poll."""
        try:
            store = self._open()
            try:
                return ObservatoryLearningSnapshot(
                    snapshots=oqc.learning_snapshots(self._repo_id, port=store),
                    facts=oqc.learning_facts(self._repo_id, port=store),
                    learning_context=oqc.learning_context(self._repo_id, port=store),
                )
            finally:
                store.close()
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ObservatoryStoreUnavailable(str(exc)) from exc

    def detail(self, assessment_id: str) -> dict[str, Any]:
        """Repo-scoped detail for one assessment, from its own short-lived session. Raises the
        controller's AssessmentNotFoundError for a missing or foreign-repo assessment."""
        try:
            store = self._open()
            try:
                detail = oqc.assessment_detail_for_repo(
                    assessment_id, self._repo_id, port=store
                )
                context = oqc.learning_context(
                    self._repo_id, [assessment_id], port=store
                )
                return {
                    **detail,
                    "learning_context": context["items"][0] if context["items"] else None,
                }
            finally:
                store.close()
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ObservatoryStoreUnavailable(str(exc)) from exc

    def explore_repository(
        self,
        repo_root: str,
        query: str,
        files: tuple[str, ...],
        explorer: RepositoryExplorer,
    ) -> tuple[LearningContextRecall, ExplorationResult]:
        """Run the M5B recall-then-current controller for one explicit TUI command."""
        store: SqliteStore | None = None
        try:
            try:
                store = self._open()
            except ObservatoryStoreUnavailable:
                store = None
            result = explore_controller.explore_repository(
                repo_root,
                self._repo_id,
                query,
                learning_port=store,
                explorer=explorer,
                files=files,
            )
            return result.learning_context, result.repository_context
        finally:
            if store is not None:
                store.close()
