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
    config_digest: str | None
    graph_scope_digest: str | None
    sync_performed: bool
    fallback_reason: str | None


def graph_snapshot_matches(
    prepared: GraphSnapshot,
    returned: GraphSnapshot,
    *,
    result_available: bool,
) -> bool:
    """Fence a provider result to the exact snapshot accepted before its query.

    An available result must return the identical snapshot. A failed/unavailable query may change
    only the snapshot status and fallback reason; its provider, versions, repo/config/scope binding,
    and sync provenance remain fixed.
    """
    if result_available:
        return returned == prepared
    return (
        returned.provider,
        returned.provider_version,
        returned.index_version,
        returned.repo_head,
        returned.config_digest,
        returned.graph_scope_digest,
        returned.sync_performed,
    ) == (
        prepared.provider,
        prepared.provider_version,
        prepared.index_version,
        prepared.repo_head,
        prepared.config_digest,
        prepared.graph_scope_digest,
        prepared.sync_performed,
    )
