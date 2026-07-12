"""sanction_store (AD-26) — reads/writes controlled-high-risk sanction events via SQLite."""

from __future__ import annotations

from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.adapters.candidate_binding import CandidateBindingAdapter
from pebra.core.models import CandidateAction


class SanctionStore:
    def __init__(self, store: SqliteStore, *, repo_root: str | None = None) -> None:
        self._store = store
        self._repo_root = repo_root
        self._bindings = CandidateBindingAdapter()

    def active_sanction(self, repo_id: str, action: CandidateAction) -> dict[str, Any] | None:
        if self._repo_root is None:
            return None
        binding = self._bindings.bind_candidate(action, self._repo_root)
        if binding is None:
            return None
        return self._store.claim_sanction_for_candidate(repo_id, action.id, binding)

    def create_sanction(self, repo_id: str, sanction: dict[str, Any]) -> str:
        return self._store.create_sanction(repo_id, sanction)
