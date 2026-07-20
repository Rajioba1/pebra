"""Trusted CodeGraph snapshot preparation regressions.

These tests exercise the subprocess control flow with an apparently-clean provider status.  A clean
status is deliberately insufficient: every accepted snapshot must follow one successful sync and
stable repository/config fences.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pebra.adapters import codegraph_adapter as cga
from pebra.core.graph_version import CODEGRAPH_ACCEPTED_RANGE


FRESH = {
    "initialized": True,
    "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
    "index": {"reindexRecommended": False},
    "version": "1.1.1",
    "worktreeMismatch": None,
}


class _Runner:
    def __init__(
        self,
        statuses: list[dict | None],
        *,
        sync_returncode: int = 0,
        sync_exception: BaseException | None = None,
        on_sync=None,
    ) -> None:
        self.statuses = list(statuses)
        self.sync_returncode = sync_returncode
        self.sync_exception = sync_exception
        self.on_sync = on_sync
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if "status" in argv:
            payload = self.statuses.pop(0)
            if payload is None:
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
        if "sync" in argv:
            if self.on_sync is not None:
                self.on_sync(len(self.sync_calls))
            if self.sync_exception is not None:
                raise self.sync_exception
            return SimpleNamespace(returncode=self.sync_returncode, stdout="")
        raise AssertionError(argv)

    @property
    def sync_calls(self) -> list[list[str]]:
        return [call for call in self.calls if "sync" in call]

    @property
    def status_calls(self) -> list[list[str]]:
        return [call for call in self.calls if "status" in call]


def _patch_runtime(monkeypatch, runner: _Runner, heads: list[str | None]) -> None:
    head_values = iter(heads)
    monkeypatch.setattr(cga, "find_engine", lambda: "/tools/codegraph")
    monkeypatch.setattr(cga.subprocess, "run", runner)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: next(head_values))


def test_apparently_clean_status_still_syncs_once_and_caches_snapshot(tmp_path, monkeypatch) -> None:
    runner = _Runner([FRESH, FRESH])
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])
    adapter = cga.CodeGraphAdapter()

    first = adapter.prepare(str(tmp_path))
    second = adapter.prepare(str(tmp_path))

    assert first is second
    assert first.status == "available"
    assert first.repo_head == "commit-b"
    assert first.config_digest == "absent"
    assert first.graph_scope_digest
    assert first.sync_performed is True
    assert len(runner.sync_calls) == 1
    assert len(runner.status_calls) == 2


@pytest.mark.parametrize(
    "initial",
    [
        {"initialized": False},
        {**FRESH, "worktreeMismatch": {"worktreeRoot": "A", "indexRoot": "elsewhere"}},
    ],
)
def test_uninitialized_or_worktree_mismatched_index_never_syncs(
    tmp_path, monkeypatch, initial
) -> None:
    runner = _Runner([initial])
    _patch_runtime(monkeypatch, runner, ["commit-b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert runner.sync_calls == []


def test_absent_engine_never_spawns_or_syncs(tmp_path, monkeypatch) -> None:
    runner = _Runner([])
    monkeypatch.setattr(cga, "find_engine", lambda: None)
    monkeypatch.setattr(cga.subprocess, "run", runner)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: "commit-b")

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert runner.calls == []


def test_non_git_root_without_head_never_syncs_or_returns_trusted_snapshot(
    tmp_path, monkeypatch
) -> None:
    runner = _Runner([])
    _patch_runtime(monkeypatch, runner, [None])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.repo_head is None
    assert snapshot.sync_performed is False
    assert snapshot.fallback_reason == "repository HEAD unavailable"
    assert runner.calls == []


def test_absent_index_status_never_syncs(tmp_path, monkeypatch) -> None:
    runner = _Runner([None])
    _patch_runtime(monkeypatch, runner, ["commit-b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert runner.sync_calls == []


@pytest.mark.parametrize(
    "malformed",
    [
        {},
        {**FRESH, "initialized": 1},
        {**FRESH, "pendingChanges": []},
        {**FRESH, "pendingChanges": {"added": -1, "modified": 0, "removed": 0}},
        {**FRESH, "index": []},
        {**FRESH, "index": {"reindexRecommended": 0}},
        {key: value for key, value in FRESH.items() if key != "version"},
    ],
)
def test_malformed_initial_status_is_unavailable_and_never_syncs(
    tmp_path, monkeypatch, malformed
) -> None:
    runner = _Runner([malformed])
    _patch_runtime(monkeypatch, runner, ["commit-b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert runner.sync_calls == []


def test_malformed_post_sync_status_is_unavailable_and_never_cached(
    tmp_path, monkeypatch
) -> None:
    runner = _Runner([FRESH, {}])
    _patch_runtime(monkeypatch, runner, ["commit-b"])
    adapter = cga.CodeGraphAdapter()

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert adapter.prepared_status(str(tmp_path)) is None
    assert len(runner.sync_calls) == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        False,
        0,
        "",
        [],
        {},
        {"worktreeRoot": "A"},
        {"worktreeRoot": "A", "indexRoot": ""},
    ],
)
def test_falsey_or_malformed_initial_worktree_mismatch_never_syncs(
    tmp_path, monkeypatch, mismatch
) -> None:
    initial = {**FRESH, "worktreeMismatch": mismatch}
    runner = _Runner([initial, FRESH])
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert runner.sync_calls == []


@pytest.mark.parametrize(
    "mismatch",
    [
        False,
        0,
        "",
        [],
        {},
        {"worktreeRoot": "A"},
        {"worktreeRoot": "A", "indexRoot": ""},
        {"worktreeRoot": "A", "indexRoot": "B"},
    ],
)
def test_any_non_null_post_sync_worktree_mismatch_is_unavailable(
    tmp_path, monkeypatch, mismatch
) -> None:
    post = {**FRESH, "worktreeMismatch": mismatch}
    runner = _Runner([FRESH, post])
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])
    adapter = cga.CodeGraphAdapter()

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert adapter.prepared_status(str(tmp_path)) is None
    assert len(runner.sync_calls) == 1


@pytest.mark.parametrize("phase", ["initial", "post"])
def test_missing_worktree_mismatch_field_is_unavailable(tmp_path, monkeypatch, phase) -> None:
    missing = {key: value for key, value in FRESH.items() if key != "worktreeMismatch"}
    statuses = [missing] if phase == "initial" else [FRESH, missing]
    runner = _Runner(statuses)
    heads = ["commit-b"] if phase == "initial" else ["commit-b", "commit-b"]
    _patch_runtime(monkeypatch, runner, heads)

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert len(runner.sync_calls) == (0 if phase == "initial" else 1)


def test_unsupported_initial_provider_version_never_syncs_or_caches(
    tmp_path, monkeypatch
) -> None:
    unsupported = {**FRESH, "version": "2.0.0"}
    runner = _Runner([unsupported, FRESH])
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])
    adapter = cga.CodeGraphAdapter()

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert snapshot.fallback_reason == (
        f"codegraph version outside the accepted range {CODEGRAPH_ACCEPTED_RANGE}; "
        "run: pebra setup-graph --fix"
    )
    assert adapter.prepared_status(str(tmp_path)) is None
    assert runner.sync_calls == []


def test_unsupported_post_sync_provider_version_is_unavailable_and_not_cached(
    tmp_path, monkeypatch
) -> None:
    unsupported = {**FRESH, "version": "2.0.0"}
    runner = _Runner([FRESH, unsupported])
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])
    adapter = cga.CodeGraphAdapter()

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is True
    assert snapshot.fallback_reason == (
        f"codegraph version outside the accepted range {CODEGRAPH_ACCEPTED_RANGE}; "
        "run: pebra setup-graph --fix"
    )
    assert adapter.prepared_status(str(tmp_path)) is None
    assert len(runner.sync_calls) == 1


def test_injected_unsupported_provider_version_is_unavailable_and_not_cached(tmp_path) -> None:
    unsupported = {**FRESH, "version": "2.0.0"}
    adapter = cga.CodeGraphAdapter(status_fn=lambda _root: unsupported)

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.sync_performed is False
    assert snapshot.fallback_reason == (
        f"codegraph version outside the accepted range {CODEGRAPH_ACCEPTED_RANGE}; "
        "run: pebra setup-graph --fix"
    )
    assert adapter.prepared_status(str(tmp_path)) is None


def test_injected_malformed_status_is_unavailable_and_never_cached(tmp_path) -> None:
    adapter = cga.CodeGraphAdapter(status_fn=lambda _root: {})

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert adapter.prepared_status(str(tmp_path)) is None


def test_unreadable_config_digest_is_unavailable_and_never_spawns(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "codegraph.json"
    config.write_text("{}", encoding="utf-8")
    runner = _Runner([])
    original_read_bytes = Path.read_bytes

    def deny_config_read(path: Path) -> bytes:
        if path == config:
            raise PermissionError("denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_config_read)
    monkeypatch.setattr(cga, "find_engine", lambda: "/tools/codegraph")
    monkeypatch.setattr(cga.subprocess, "run", runner)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: "commit-b")

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.config_digest is None
    assert snapshot.graph_scope_digest is None
    assert runner.calls == []


@pytest.mark.parametrize("sync_returncode,post_status", [(9, FRESH), (0, None)])
def test_sync_failure_never_falls_back_to_initial_clean_status(
    tmp_path, monkeypatch, sync_returncode, post_status
) -> None:
    statuses = [FRESH] if sync_returncode else [FRESH, post_status]
    runner = _Runner(statuses, sync_returncode=sync_returncode)
    _patch_runtime(monkeypatch, runner, ["commit-b", "commit-b"])
    adapter = cga.CodeGraphAdapter()

    snapshot = adapter.prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert adapter.prepared_status(str(tmp_path)) is None
    assert len(runner.sync_calls) == 1


def test_sync_timeout_never_falls_back_to_initial_clean_status(tmp_path, monkeypatch) -> None:
    runner = _Runner(
        [FRESH],
        sync_exception=cga.subprocess.TimeoutExpired(["codegraph", "sync"], 120),
    )
    _patch_runtime(monkeypatch, runner, ["commit-b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "unavailable"
    assert snapshot.fallback_reason == "codegraph sync failed"


def test_head_fence_moves_once_then_retries_and_accepts_second_attempt(tmp_path, monkeypatch) -> None:
    runner = _Runner([FRESH, FRESH, FRESH, FRESH])
    _patch_runtime(monkeypatch, runner, ["a", "b", "b", "b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "available"
    assert snapshot.repo_head == "b"
    assert len(runner.sync_calls) == 2


def test_head_fence_moves_twice_returns_stale_after_two_attempts(tmp_path, monkeypatch) -> None:
    runner = _Runner([FRESH, FRESH, FRESH, FRESH])
    _patch_runtime(monkeypatch, runner, ["a", "b", "b", "c"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "stale"
    assert snapshot.repo_head is None
    assert len(runner.sync_calls) == 2


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, b'{"exclude":["generated/**"]}'),
        (b'{"exclude":[]}', b'{"exclude":["generated/**"]}'),
        (b'{"exclude":["generated/**"]}', None),
    ],
)
def test_codegraph_config_add_change_remove_retries_then_records_new_scope(
    tmp_path: Path, monkeypatch, before: bytes | None, after: bytes | None
) -> None:
    config = tmp_path / "codegraph.json"
    if before is not None:
        config.write_bytes(before)

    def mutate(sync_count: int) -> None:
        if sync_count != 1:
            return
        if after is None:
            config.unlink()
        else:
            config.write_bytes(after)

    runner = _Runner([FRESH, FRESH, FRESH, FRESH], on_sync=mutate)
    _patch_runtime(monkeypatch, runner, ["b", "b", "b", "b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "available"
    assert snapshot.config_digest == (
        "absent" if after is None else cga.hashlib.sha256(after).hexdigest()
    )
    assert snapshot.graph_scope_digest
    assert len(runner.sync_calls) == 2


def test_config_fence_moves_twice_returns_stale_after_two_attempts(tmp_path, monkeypatch) -> None:
    config = tmp_path / "codegraph.json"
    config.write_bytes(b"one")

    def mutate(sync_count: int) -> None:
        config.write_bytes(b"two" if sync_count == 1 else b"three")

    runner = _Runner([FRESH, FRESH, FRESH, FRESH], on_sync=mutate)
    _patch_runtime(monkeypatch, runner, ["b", "b", "b", "b"])

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "stale"
    assert len(runner.sync_calls) == 2


def test_graph_scope_digest_survives_ordinary_commit_changes(tmp_path, monkeypatch) -> None:
    adapter_a = cga.CodeGraphAdapter(status_fn=lambda _root: FRESH)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: "a")
    first = adapter_a.prepare(str(tmp_path))
    adapter_b = cga.CodeGraphAdapter(status_fn=lambda _root: FRESH)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: "b")
    second = adapter_b.prepare(str(tmp_path))

    assert first.repo_head != second.repo_head
    assert first.graph_scope_digest == second.graph_scope_digest


def test_windows_cmd_launcher_is_used_for_status_and_sync(tmp_path, monkeypatch) -> None:
    from pebra.core import engine_argv

    runner = _Runner([FRESH, FRESH])
    monkeypatch.setattr(cga, "find_engine", lambda: r"C:\tools\codegraph.cmd")
    monkeypatch.setattr(cga.subprocess, "run", runner)
    monkeypatch.setattr(cga.git_adapter, "head_commit", lambda _root: "b")
    monkeypatch.setattr(engine_argv.os, "name", "nt")

    snapshot = cga.CodeGraphAdapter().prepare(str(tmp_path))

    assert snapshot.status == "available"
    assert all(call[:3] == ["cmd", "/c", r"C:\tools\codegraph.cmd"] for call in runner.calls)
