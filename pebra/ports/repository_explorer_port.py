"""Port for bounded, descriptive repository exploration."""

from __future__ import annotations

from typing import Protocol

from pebra.core.exploration import ExplorationResult
from pebra.core.graph_snapshot import GraphSnapshot


class RepositoryExplorer(Protocol):
    def prepare(self, repo_root: str) -> GraphSnapshot:
        """Reconcile an existing same-worktree graph index."""

    def explore(
        self,
        repo_root: str,
        query: str,
        *,
        snapshot: GraphSnapshot,
        files: tuple[str, ...] = (),
        max_files: int = 8,
        max_bytes: int = 24_000,
    ) -> ExplorationResult:
        """Return bounded descriptive context for one prepared snapshot."""
