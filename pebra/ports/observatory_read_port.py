"""ObservatoryReadPort (Observatory TUI M1) — the read-only store contract the Observatory query
controller depends on.

Both surfaces that show assessment history — the FastAPI dashboard and the Textual TUI — read through
``pebra.app.observatory_query_controller``, which depends only on this port. ``SqliteStore`` satisfies it
structurally, so the app-layer controller never imports the adapter (the app-no-adapters contract holds).
It is strictly read-only: every method below is a persisted projection and none mutates.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from pebra.core.learning_context import LearningContextEntry


class ObservatoryReadPort(Protocol):
    def assessment_facets(self, repo_id: str) -> Iterable[dict[str, Any]]:
        """Decision/status pairs for every assessment in a repo, used for exact overview counts."""
        ...

    def list_assessments(
        self, repo_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Newest-first summaries with terminal status and projected assessment identity."""
        ...

    def assessment_detail(self, assessment_id: str) -> dict[str, Any]:
        """Full detail for one assessment. Raises KeyError when the assessment does not exist."""
        ...

    def chain_status(self) -> dict[str, Any]:
        """Store-wide audit-chain verdict + per-table row counts (database-global, not repo-scoped)."""
        ...

    def list_risk_snapshots(self, repo_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first persisted learning snapshots for one repository."""
        ...

    def list_learned_risk_facts(
        self, repo_id: str, snapshot_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Newest-first persisted learned facts for one repository and optional snapshot."""
        ...

    def assessment_prior_facets(
        self, repo_id: str, assessment_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Persisted applied-prior summaries for visible assessment rows in one repository."""
        ...

    def list_learning_context(
        self,
        repo_id: str,
        assessment_ids: Sequence[str] | None = None,
        limit: int = 200,
    ) -> list[LearningContextEntry]:
        """Verified lessons for a repository, optionally batched over visible assessment IDs."""
        ...
