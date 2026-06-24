"""sanction_store (Phase-0 SanctionPort, AD-26) — reads/writes sanction events via the SQLite store.

Phase 0: ``active_sanction`` returns ``None`` (cold start — no active sanctions), so gate-10 never
converts a decision. ``create_sanction`` is wired for the accept-risk surface.
"""

from __future__ import annotations

from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.core.models import CandidateAction


class SanctionStore:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def active_sanction(self, repo_id: str, action: CandidateAction) -> dict[str, Any] | None:
        return None

    def create_sanction(self, repo_id: str, sanction: dict[str, Any]) -> str:
        return self._store.create_sanction(repo_id, sanction)
