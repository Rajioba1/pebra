"""Provider-neutral, bounded repository exploration results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


def normalize_repository_files(repo_root: str, files: tuple[str, ...]) -> tuple[str, ...]:
    root = Path(repo_root).resolve()
    normalized: list[str] = []
    seen: set[str] = set()
    for value in files:
        # Provider-facing repository paths use POSIX separators on every host. Normalize Windows
        # input lexically first so the same candidate is neither duplicated nor misread on POSIX.
        candidate = Path(value.replace("\\", "/"))
        try:
            resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        path = relative.as_posix()
        if path and path != "." and path not in seen:
            seen.add(path)
            normalized.append(path)
    return tuple(normalized)


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
