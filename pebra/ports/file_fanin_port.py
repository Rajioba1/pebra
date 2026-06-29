"""FileFanInProvider (Architecture §3) — Protocol contract only.

Aggregate call-graph fan-in across ALL callable symbols in a file, for whole-file destructive ops
(DELETE). Distinct from the per-symbol FanInProvider. Fail-soft: returns an 'unresolved' rollup when
the graph engine / DB is absent or stale (never raises on the assess path).
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import FileFanInRollup


class FileFanInProvider(Protocol):
    def file_fanin_rollup(self, file_path: str, repo_root: str) -> FileFanInRollup:
        """Aggregate fan-in for all callable symbols in ``file_path`` (repo-relative). Fail-soft."""
        ...
