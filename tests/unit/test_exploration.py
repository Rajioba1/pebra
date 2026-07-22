from __future__ import annotations

from dataclasses import replace

import pytest

from pebra.core.exploration import (
    MAX_CONTEXT_BYTES,
    MAX_FILES,
    MIN_CONTEXT_BYTES,
    MIN_FILES,
    bounded_context,
    clamp_bounds,
    unavailable_result,
)
from pebra.core.graph_snapshot import GraphSnapshot, graph_snapshot_matches
from pebra.ports.repository_explorer_port import RepositoryExplorer


def _snapshot(status: str = "unavailable") -> GraphSnapshot:
    return GraphSnapshot(
        status=status,
        provider=None,
        provider_version=None,
        index_version=None,
        repo_head=None,
        config_digest="absent",
        graph_scope_digest=None,
        sync_performed=False,
        fallback_reason="graph unavailable",
    )


@pytest.mark.parametrize("status", ["unavailable", "stale", "error"])
def test_available_result_requires_an_available_prepared_snapshot(status: str) -> None:
    snapshot = _snapshot(status)

    assert graph_snapshot_matches(snapshot, snapshot, result_available=True) is False


def test_unavailable_result_keeps_the_existing_status_and_reason_exception() -> None:
    prepared = _snapshot("unavailable")
    returned = replace(prepared, status="error", fallback_reason="query failed")

    assert graph_snapshot_matches(prepared, returned, result_available=False) is True


@pytest.mark.parametrize(
    ("requested_files", "requested_bytes", "expected"),
    [
        (-5, 10, (MIN_FILES, MIN_CONTEXT_BYTES)),
        (8, 24_000, (8, 24_000)),
        (99, 999_999, (MAX_FILES, MAX_CONTEXT_BYTES)),
    ],
)
def test_exploration_bounds_are_clamped(requested_files, requested_bytes, expected) -> None:
    assert clamp_bounds(requested_files, requested_bytes) == expected


def test_bounded_context_never_splits_utf8_and_marks_truncation() -> None:
    context, truncated = bounded_context("a" * 999 + "💥tail", 1_000)

    assert context == "a" * 999
    assert len(context.encode("utf-8")) <= 1_000
    assert truncated is True


def test_bounded_context_preserves_complete_utf8_without_truncation() -> None:
    context, truncated = bounded_context("repository 💥", 1_000)

    assert context == "repository 💥"
    assert truncated is False


def test_unavailable_result_never_fabricates_context_files_or_tests() -> None:
    snapshot = _snapshot()

    result = unavailable_result(snapshot, "provider unavailable")

    assert result.status == "unavailable"
    assert result.snapshot is snapshot
    assert result.context == ""
    assert result.dependent_files == ()
    assert result.affected_tests == ()
    assert result.warnings == ()
    assert result.fallback_reason == "provider unavailable"
    assert result.truncated is False


def test_repository_explorer_is_a_structural_port() -> None:
    assert RepositoryExplorer.__module__ == "pebra.ports.repository_explorer_port"
