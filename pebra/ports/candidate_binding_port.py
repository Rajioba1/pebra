"""Port for deriving a host-enforceable identity from a proposed candidate edit."""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import CandidateAction


class CandidateBindingProvider(Protocol):
    def bind_candidate(self, action: CandidateAction, repo_root: str) -> dict[str, Any] | None:
        """Return normalized resulting-content hashes, or None when the patch cannot be bound."""

    def bind_baseline(self, action: CandidateAction, repo_root: str) -> dict[str, Any] | None:
        """Return a host-derived binding for the complete non-ignored working-tree baseline."""
