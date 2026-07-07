from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pebra.adapters.codegraph_materialized_diff import CodeGraphMaterializedDiffAdapter
from pebra.core.models import MaterializedGraphDiffResult


def _make_db(
    path: Path,
    rows: list[tuple[str, str, str | None, str | None, str | None]],
    *,
    language: str = "typescript",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            kind TEXT,
            qualified_name TEXT,
            file_path TEXT,
            language TEXT,
            signature TEXT,
            visibility TEXT,
            return_type TEXT
        );
        """
    )
    for i, (file_path, qualified_name, signature, visibility, return_type) in enumerate(rows):
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
            (
                f"n{i}",
                "function",
                qualified_name,
                file_path,
                language,
                signature,
                visibility,
                return_type,
            ),
        )
    con.commit()
    con.close()


def test_materialized_diff_is_dark_gated_by_default(tmp_path: Path) -> None:
    adapter = CodeGraphMaterializedDiffAdapter(enabled=False)

    result = adapter.diff(
        before_files={"src/a.ts": "export function f(x: number) { return x }"},
        after_files={"src/a.ts": "export function f(x: string) { return x }"},
        repo_root=str(tmp_path),
    )

    assert isinstance(result, MaterializedGraphDiffResult)
    assert result.available is False
    assert result.rows == ()
    assert result.fallback_reason == "materialized CodeGraph diff disabled"


def test_materialized_diff_matches_by_file_and_qualified_name_not_node_id(tmp_path: Path) -> None:
    dbs: list[Path] = []

    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        dbs.append(db_path)
        if len(dbs) == 1:
            _make_db(db_path, [("src/a.ts", "f", "(x: number) => number", "public", "number")])
        else:
            # Different node id, same stable file+qualified name.
            _make_db(db_path, [("src/a.ts", "f", "(x: string) => string", "private", "string")])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/a.ts": "before"},
        after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is True
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.file_path == "src/a.ts"
    assert row.qualified_name == "f"
    assert row.signature_changed is True
    assert row.return_type_changed is True
    assert row.visibility_changed is True
    assert result.latency_ms >= 0.0


def test_materialized_diff_supports_signature_language_without_visibility(tmp_path: Path) -> None:
    dbs: list[Path] = []

    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        dbs.append(db_path)
        if len(dbs) == 1:
            _make_db(db_path, [("src/a.go", "f", "func f(x int) int", None, "int")], language="go")
        else:
            _make_db(db_path, [("src/a.go", "f", "func f(x string) string", None, "string")],
                     language="go")
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/a.go": "before"},
        after_files={"src/a.go": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is True
    assert result.rows[0].language == "go"
    assert result.rows[0].signature_changed is True
    assert result.rows[0].visibility_changed is None


def test_materialized_diff_does_not_treat_null_vs_null_as_unchanged(tmp_path: Path) -> None:
    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [("src/a.ts", "f", None, "public", None)])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/a.ts": "before"},
        after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.rows == ()
    assert result.fallback_reason == "no comparable semantic fields"


def test_materialized_diff_keeps_csharp_signatureless_shape_unavailable(tmp_path: Path) -> None:
    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [("src/A.cs", "A.M", None, "public", "int")], language="csharp")
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/A.cs": "before"},
        after_files={"src/A.cs": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.fallback_reason == "no comparable semantic fields"


def test_materialized_diff_no_owner_nodes_is_unavailable(tmp_path: Path) -> None:
    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"README.md": "before"},
        after_files={"README.md": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.fallback_reason == "no comparable semantic fields"


@pytest.mark.parametrize("bad_path", ["../outside.ts", "src/../outside.ts", "/tmp/outside.ts",
                                      "C:/tmp/outside.ts", ""])
def test_materialized_diff_rejects_paths_that_escape_temp_root(tmp_path: Path, bad_path: str) -> None:
    def should_not_index(root: Path) -> Path:
        raise AssertionError("indexer must not run for an invalid materialized path")

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=should_not_index)

    result = adapter.diff(
        before_files={bad_path: "before"},
        after_files={bad_path: "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.fallback_reason == "invalid materialized file path"


def test_materialized_diff_rejects_duplicate_stable_owner_keys(tmp_path: Path) -> None:
    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [
            ("src/a.ts", "f", "(x: number) => number", "public", "number"),
            ("src/a.ts", "f", "(x: string) => string", "public", "string"),
        ])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/a.ts": "before"},
        after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.fallback_reason == "materialized owner key ambiguous"


def test_materialized_diff_fails_unavailable_when_owner_sets_do_not_match(tmp_path: Path) -> None:
    dbs: list[Path] = []

    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        dbs.append(db_path)
        if len(dbs) == 1:
            _make_db(db_path, [("src/a.ts", "f", "(x: number) => number", "public", "number")])
        else:
            _make_db(db_path, [("src/a.ts", "g", "(x: number) => number", "public", "number")])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)

    result = adapter.diff(
        before_files={"src/a.ts": "before"},
        after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is False
    assert result.fallback_reason == "materialized owner mismatch"
