"""Immutable provenance for one reconciled repository graph snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GraphSnapshotStatus = Literal["available", "unavailable", "stale", "error"]


@dataclass(frozen=True)
class GraphSnapshot:
    status: GraphSnapshotStatus
    provider: str | None
    provider_version: str | None
    index_version: str | None
    repo_head: str | None
    config_digest: str
    graph_scope_digest: str | None
    sync_performed: bool
    fallback_reason: str | None
