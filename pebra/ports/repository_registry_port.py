"""RepositoryRegistryPort (Architecture §3, AD-24). Protocol contract only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepoMetadata:
    repo_id: str
    repo_root: str


class RepositoryRegistryPort(Protocol):
    def resolve(self, start_path: str) -> RepoMetadata:
        """Walk up from start_path to the repo root and return its stable repo_id + root."""
        ...
