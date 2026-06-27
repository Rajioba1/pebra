"""SnapshotReadPort (M5c) — read-only contract for the active learned snapshot.

The assess path fetches the active SnapshotBundle through this port and hands it to the pure
``apply_snapshot`` pre-scoring. It is strictly READ-ONLY: the assess path performs no learning write
and imports no learning-writer module (the Hard Rule, evolved for M5c). The concrete adapter lives in
``adapters/snapshot_read_store.py`` — deliberately NOT in learning_store/calibration_store — so the
assess surfaces can use it via this port without breaching the assess-no-learning contract.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.apply_snapshot import SnapshotBundle


class SnapshotReadPort(Protocol):
    def load_active_snapshot(self, repo_id: str) -> SnapshotBundle | None:
        """Return the active SnapshotBundle for a repo (applicable facts only), or None when no
        snapshot is promoted."""
        ...
