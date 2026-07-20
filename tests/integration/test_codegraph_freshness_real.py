"""Pinned-provider proof that clean Git/config transitions are reconciled."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine

pytestmark = pytest.mark.requires_codegraph


def _run(argv: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=timeout,
    )


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _names(repo: Path, adapter: CodeGraphAdapter) -> set[str]:
    status = adapter.prepared_status(str(repo))
    assert status is not None
    index_path = Path(status.get("indexPath") or (repo / ".codegraph"))
    con = sqlite3.connect(str(index_path / "codegraph.db"))
    try:
        return {str(row[0]) for row in con.execute("SELECT name FROM nodes")}
    finally:
        con.close()


def test_clean_checkout_and_config_only_transitions_reconcile_real_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", str(repo)])
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "src" / "modified.py").write_text(
        "def modified_at_a():\n    return 'a'\n", encoding="utf-8"
    )
    (repo / "src" / "deleted.py").write_text(
        "def deleted_at_b():\n    return 'gone'\n", encoding="utf-8"
    )
    (repo / "src" / "excluded.py").write_text(
        "def excluded_symbol():\n    return 1\n", encoding="utf-8"
    )
    (repo / "src" / "custom.extx").write_text(
        "def custom_extension_symbol():\n    return 2\n", encoding="utf-8"
    )
    commit_a = _commit(repo, "A")

    exe = find_engine()
    assert exe is not None
    _run(resolve_engine_argv(exe, ["init", str(repo)]))

    (repo / "src" / "modified.py").write_text(
        "def modified_at_b():\n    return 'b'\n", encoding="utf-8"
    )
    (repo / "src" / "deleted.py").unlink()
    (repo / "src" / "added.py").write_text(
        "def added_at_b():\n    return 'new'\n", encoding="utf-8"
    )
    commit_b = _commit(repo, "B")
    _git(repo, "checkout", commit_a)
    _git(repo, "checkout", commit_b)
    assert _git(repo, "status", "--porcelain") == ""

    checkout_adapter = CodeGraphAdapter()
    checkout_snapshot = checkout_adapter.prepare(str(repo))
    checkout_names = _names(repo, checkout_adapter)

    assert checkout_snapshot.status == "available", checkout_snapshot.fallback_reason
    assert checkout_snapshot.repo_head == commit_b
    assert checkout_snapshot.sync_performed is True
    assert "added_at_b" in checkout_names
    assert "modified_at_b" in checkout_names
    assert "modified_at_a" not in checkout_names
    assert "deleted_at_b" not in checkout_names

    (repo / "codegraph.json").write_text(
        json.dumps({"extensions": {".extx": "python"}}), encoding="utf-8"
    )
    added_config_commit = _commit(repo, "add graph config")
    added_config_adapter = CodeGraphAdapter()
    added_config_snapshot = added_config_adapter.prepare(str(repo))
    assert added_config_snapshot.repo_head == added_config_commit
    assert "custom_extension_symbol" in _names(repo, added_config_adapter)

    (repo / "codegraph.json").write_text(
        json.dumps({
            "extensions": {".extx": "python"},
            "includeIgnored": ["vendor/**"],
        }),
        encoding="utf-8",
    )
    _commit(repo, "change graph config")
    changed_adapter = CodeGraphAdapter()
    changed_snapshot = changed_adapter.prepare(str(repo))
    changed_names = _names(repo, changed_adapter)
    assert changed_snapshot.config_digest != added_config_snapshot.config_digest
    assert "excluded_symbol" in changed_names
    assert "custom_extension_symbol" in changed_names

    (repo / "codegraph.json").unlink()
    _commit(repo, "remove graph config")
    removed_adapter = CodeGraphAdapter()
    removed_snapshot = removed_adapter.prepare(str(repo))
    removed_names = _names(repo, removed_adapter)
    assert removed_snapshot.config_digest == "absent"
    assert "excluded_symbol" in removed_names
    assert "added_at_b" in removed_names
    assert "custom_extension_symbol" not in removed_names
