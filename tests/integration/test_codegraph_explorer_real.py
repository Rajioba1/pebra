"""Pinned-provider mutation and bounded exploration proof."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.adapters.codegraph_explorer import CodeGraphExplorer
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine


pytestmark = pytest.mark.requires_codegraph


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True, timeout=180,
    )


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _outside(repo: Path, cache: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(repo).as_posix(): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in repo.rglob("*")
        if path.is_file() and cache not in path.parents
    }


def _db_identity(db: Path) -> tuple[str, int, int, str]:
    stat = db.stat()
    return (
        str(db.resolve()), stat.st_size, stat.st_mtime_ns,
        hashlib.sha256(db.read_bytes()).hexdigest(),
    )


def _persistent_snapshot(cache: Path) -> dict[str, tuple[str, int, int, str]]:
    root = cache.resolve()
    return {
        path.relative_to(root).as_posix(): (
            str(path.resolve()),
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"codegraph.db-wal", "codegraph.db-shm"}
    }


def _logical_digest(db: Path, copy: Path) -> tuple[str, str, int]:
    shutil.copyfile(db, copy)
    connection = sqlite3.connect(f"{copy.resolve().as_uri()}?mode=ro", uri=True)
    try:
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name, tbl_name, sql"
        ).fetchall()
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        logical: dict[str, list[str]] = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            logical[table] = sorted(
                repr(row) for row in connection.execute(f"SELECT * FROM {quoted}").fetchall()
            )
    finally:
        connection.close()
    schema_hash = hashlib.sha256(repr(schema).encode()).hexdigest()
    logical_hash = hashlib.sha256(
        json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return schema_hash, logical_hash, sum(len(rows) for rows in logical.values())


def test_real_explorer_preserves_repository_and_logical_graph_state(tmp_path) -> None:
    repo = tmp_path / "repo"
    diagnostics = tmp_path / "diagnostics"
    repo.mkdir()
    diagnostics.mkdir()
    (repo / "pebra").mkdir()
    (repo / "pebra" / "observatory_context.py").write_text(
        "def repository_resolution():\n    return 'resolved'\n", encoding="utf-8"
    )
    (repo / "pebra" / "consumer.py").write_text(
        "from pebra.observatory_context import repository_resolution\n\n"
        "def consume():\n    return repository_resolution()\n",
        encoding="utf-8",
    )
    (repo / ".pebra").mkdir()
    (repo / ".pebra" / "sentinel.txt").write_text("sentinel\n", encoding="utf-8")
    config = b'{"includeIgnored":[]}\n'
    (repo / "codegraph.json").write_bytes(config)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "PEBRA Test")
    _git(repo, "config", "user.email", "noreply@example.com")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", "fixture")
    head = _git(repo, "rev-parse", "HEAD")
    engine = find_engine()
    assert engine is not None
    _run(resolve_engine_argv(engine, ["init", str(repo)]))
    cache = repo / ".codegraph"
    outside_before_prepare = _outside(repo, cache)

    graph = CodeGraphAdapter()
    explorer = CodeGraphExplorer(graph_adapter=graph)
    snapshot = explorer.prepare(str(repo))

    assert snapshot.status == "available", snapshot.fallback_reason
    assert snapshot.repo_head == head
    assert snapshot.index_version == "24"
    assert _outside(repo, cache) == outside_before_prepare
    assert not list(cache.glob("codegraph.db-*") )

    db = cache / "codegraph.db"
    outside_before_query = _outside(repo, cache)
    persistent_before_query = _persistent_snapshot(cache)
    db_before = _db_identity(db)
    graph_before = _logical_digest(db, diagnostics / "before.db")

    result = explorer.explore(
        str(repo), "repository resolution", snapshot=snapshot,
        files=("pebra/observatory_context.py",), max_files=2, max_bytes=1_000,
    )

    assert result.status == "available", result.fallback_reason
    assert result.snapshot == snapshot
    assert len(result.context.encode("utf-8")) <= 1_000
    assert "repository_resolution" in result.context
    assert _outside(repo, cache) == outside_before_query
    assert _persistent_snapshot(cache) == persistent_before_query
    assert _db_identity(db) == db_before
    assert _logical_digest(db, diagnostics / "after.db") == graph_before
    assert _git(repo, "rev-parse", "HEAD") == head
    assert hashlib.sha256((repo / "codegraph.json").read_bytes()).hexdigest() == snapshot.config_digest
    assert not list(cache.glob("codegraph.db-*") )
