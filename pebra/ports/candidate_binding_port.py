"""Port for deriving a host-enforceable identity from a proposed candidate edit."""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import CandidateAction


class CandidateBindingProvider(Protocol):
    def bind_candidate(self, action: CandidateAction, repo_root: str) -> dict[str, Any] | None:
        """Return normalized resulting-content hashes, or None when the patch cannot be bound."""
