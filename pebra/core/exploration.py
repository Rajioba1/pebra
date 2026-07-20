"""Provider-neutral, bounded repository exploration results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pebra.core.graph_snapshot import GraphSnapshot


MIN_FILES = 1
MAX_FILES = 32
MIN_CONTEXT_BYTES = 1_000
MAX_CONTEXT_BYTES = 100_000

ExplorationStatus = Literal["available", "unavailable", "stale", "error"]


@dataclass(frozen=True)
class ExplorationResult:
    status: ExplorationStatus
    snapshot: GraphSnapshot
    context: str
    dependent_files: tuple[str, ...]
    affected_tests: tuple[str, ...]
    warnings: tuple[str, ...]
    fallback_reason: str | None
    truncated: bool


def clamp_bounds(max_files: int, max_bytes: int) -> tuple[int, int]:
    return (
        min(MAX_FILES, max(MIN_FILES, max_files)),
        min(MAX_CONTEXT_BYTES, max(MIN_CONTEXT_BYTES, max_bytes)),
    )


def bounded_context(context: str, max_bytes: int) -> tuple[str, bool]:
    encoded = context.encode("utf-8")
    if len(encoded) <= max_bytes:
        return context, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def unavailable_result(
    snapshot: GraphSnapshot,
    reason: str,
    *,
    warnings: tuple[str, ...] = (),
) -> ExplorationResult:
    status: ExplorationStatus = (
        snapshot.status if snapshot.status in ("stale", "error") else "unavailable"
    )
    return ExplorationResult(
        status=status,
        snapshot=snapshot,
        context="",
        dependent_files=(),
        affected_tests=(),
        warnings=warnings,
        fallback_reason=reason,
        truncated=False,
    )
