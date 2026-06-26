"""ArchitectureKnowledgeProvider port (Architecture §3, AD-22). Protocol contract only.

Builds PEBRA's own architecture map from a repo scan and reports structural signals + freshness.
The implementation (adapters/architecture_map.py) attempts a rebuild when stale; only an unresolved
stale map (rebuild failed) should slow an assessment down.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import ArchitectureEvidence


class ArchitectureKnowledgeProvider(Protocol):
    def gather_architecture(
        self, repo_root: str, affected_files: list[str], current_head: str | None
    ) -> ArchitectureEvidence: ...
