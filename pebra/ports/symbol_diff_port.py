"""SymbolDiffProvider port (Architecture §3, AD-27). Protocol contract only."""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import CandidateAction, SymbolDiffEvidence


class SymbolDiffProvider(Protocol):
    def symbol_diff(self, action: CandidateAction, repo_root: str) -> SymbolDiffEvidence: ...
