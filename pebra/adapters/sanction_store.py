"""sanction_store (AD-26) — reads/writes controlled-high-risk sanction events via SQLite."""

from __future__ import annotations

from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.core.models import CandidateAction


class SanctionStore:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def active_sanction(self, repo_id: str, action: CandidateAction) -> dict[str, Any] | None:
        return self._store.active_sanction_for_action(repo_id, action.id)

    def create_sanction(self, repo_id: str, sanction: dict[str, Any]) -> str:
        return self._store.create_sanction(repo_id, sanction)
