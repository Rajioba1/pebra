"""ObservatoryReadPort (Observatory TUI M1) — the read-only store contract the Observatory query
controller depends on.

Both surfaces that show assessment history — the FastAPI dashboard and the Textual TUI — read through
``pebra.app.observatory_query_controller``, which depends only on this port. ``SqliteStore`` satisfies it
structurally, so the app-layer controller never imports the adapter (the app-no-adapters contract holds).
It is strictly read-only: the four methods below are the entire surface, and none of them mutates.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol


class ObservatoryReadPort(Protocol):
    def assessment_facets(self, repo_id: str) -> Iterable[dict[str, Any]]:
        """Decision/status pairs for every assessment in a repo, used for exact overview counts."""
        ...

    def list_assessments(
        self, repo_id: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Newest-first assessment summaries for a repo (each carries its current terminal status,
        None = pending)."""
        ...

    def assessment_detail(self, assessment_id: str) -> dict[str, Any]:
        """Full detail for one assessment. Raises KeyError when the assessment does not exist."""
        ...

    def chain_status(self) -> dict[str, Any]:
        """Store-wide audit-chain verdict + per-table row counts (database-global, not repo-scoped)."""
        ...
