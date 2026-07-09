from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pebra.adapters.codegraph_materialized_diff import CodeGraphMaterializedDiffAdapter
from pebra.core.models import MaterializedGraphDiffResult


def _make_db(
    path: Path,
    rows: list[tuple[str, str, str | None, str | None, str | None] | dict[str, object]],
    *,
    language: str = "typescript",
    is_exported: int | None = None,
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
            return_type TEXT,
            is_exported INTEGER,
            is_abstract INTEGER
        );
        """
    )
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            file_path = str(row["file_path"])
            qualified_name = str(row["qualified_name"])
            signature = row.get("signature")
            visibility = row.get("visibility")
            return_type = row.get("return_type")
            kind = str(row.get("kind", "function"))
            row_language = str(row.get("language", language))
            row_is_exported = row.get("is_exported", is_exported)
            row_is_abstract = row.get("is_abstract")
        else:
            file_path, qualified_name, signature, visibility, return_type = row
            kind = "function"
            row_language = language
            row_is_exported = is_exported
            row_is_abstract = None
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"n{i}",
                kind,
                qualified_name,
                file_path,
                row_language,
                signature,
                visibility,
                return_type,
                row_is_exported,
                row_is_abstract,
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


def test_javascript_export_flip_registers_as_a_visibility_change(tmp_path: Path) -> None:
    # JavaScript has no getVisibility; is_exported IS its export surface. Dropping `export` leaves the
    # qualified name stable and must surface as visibility_changed via the derived values.
    dbs: list[Path] = []

    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        dbs.append(db_path)
        exported = 1 if len(dbs) == 1 else 0
        _make_db(db_path, [("src/a.js", "f", "function f(x)", None, None)],
                 language="javascript", is_exported=exported)
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(before_files={"src/a.js": "b"}, after_files={"src/a.js": "a"},
                          repo_root=str(tmp_path))

    assert result.available is True
    row = result.rows[0]
    assert row.signature_changed is False          # signature identical
    assert row.visibility_changed is True          # exported -> unexported IS a contract change


def test_go_unchanged_export_is_comparable_not_none(tmp_path: Path) -> None:
    # Both sides exported: visibility is now COMPARABLE (False), not None — coverage exists where the
    # raw null-visibility Go graph had none.
    def fake_index(root: Path) -> Path:
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [("src/a.go", "f", "func f(x int) int", None, "int")],
                 language="go", is_exported=1)
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(before_files={"src/a.go": "b"}, after_files={"src/a.go": "a"},
                          repo_root=str(tmp_path))

    assert result.available is True
    assert result.rows[0].visibility_changed is False


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

    # null-vs-null signature/return is reported as None (NOT a fabricated "False = unchanged"); the row
    # is still available because visibility was genuinely comparable (public == public -> unchanged).
    assert result.available is True
    assert result.rows[0].signature_changed is None
    assert result.rows[0].return_type_changed is None
    assert result.rows[0].visibility_changed is False


def test_materialized_diff_csharp_visibility_change_captured_without_signature(tmp_path: Path) -> None:
    # BUG-4: a signature-poor owner (signature NULL both sides) with a real visibility change must NOT
    # be discarded — the comparable visibility field is captured even though signature is unavailable.
    calls: list[int] = []

    def fake_index(root: Path) -> Path:
        calls.append(1)
        db_path = root / ".codegraph" / "codegraph.db"
        vis = "public" if len(calls) == 1 else "private"
        _make_db(db_path, [("src/A.cs", "A.M", None, vis, "int")], language="csharp")
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(
        before_files={"src/A.cs": "before"}, after_files={"src/A.cs": "after"},
        repo_root=str(tmp_path),
    )
    assert result.available is True
    assert result.rows[0].signature_changed is None  # signature genuinely not comparable
    assert result.rows[0].visibility_changed is True  # ...but the visibility change is still captured


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


def test_materialized_diff_emits_added_removed_rows_when_owner_sets_change(tmp_path: Path) -> None:
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

    assert result.available is True
    assert [(r.operation, r.qualified_name) for r in result.rows] == [("added", "g"), ("removed", "f")]
    assert {r.signature_changed for r in result.rows} == {None}


def test_materialized_diff_emits_abstract_member_addition(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_index(root: Path) -> Path:
        calls.append(1)
        db_path = root / ".codegraph" / "codegraph.db"
        rows: list[dict[str, object]] = []
        if len(calls) == 2:
            rows.append({
                "file_path": "src/a.ts",
                "qualified_name": "ZodType._pebraDescribe",
                "signature": "_pebraDescribe(): string",
                "visibility": "public",
                "return_type": "string",
                "kind": "method",
                "is_abstract": 1,
            })
        _make_db(db_path, rows)
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(
        before_files={"src/a.ts": "before"}, after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is True
    row = result.rows[0]
    assert row.operation == "added"
    assert row.kind == "method"
    assert row.is_abstract is True


def test_materialized_diff_compares_abstract_flag_on_existing_owner(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_index(root: Path) -> Path:
        calls.append(1)
        db_path = root / ".codegraph" / "codegraph.db"
        _make_db(db_path, [{
            "file_path": "src/a.ts",
            "qualified_name": "ZodType._pebraDescribe",
            "signature": "_pebraDescribe(): string",
            "visibility": "public",
            "return_type": "string",
            "kind": "method",
            "is_abstract": 1 if len(calls) == 1 else 0,
        }])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(
        before_files={"src/a.ts": "before"}, after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is True
    assert result.rows[0].operation == "modified"
    assert result.rows[0].is_abstract_changed is True


def test_truthy_or_none_unknown_sentinel_is_not_fabricated_as_true(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_index(root: Path) -> Path:
        calls.append(1)
        db_path = root / ".codegraph" / "codegraph.db"
        rows: list[dict[str, object]] = []
        if len(calls) == 2:
            rows.append({
                "file_path": "src/a.ts",
                "qualified_name": "ZodType._pebraDescribe",
                "signature": "_pebraDescribe(): string",
                "visibility": "public",
                "return_type": "string",
                "kind": "method",
                "is_abstract": "nonabstract",
            })
        _make_db(db_path, rows)
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff(
        before_files={"src/a.ts": "before"}, after_files={"src/a.ts": "after"},
        repo_root=str(tmp_path),
    )

    assert result.available is True
    assert result.rows[0].is_abstract is None


# --- P0: diff_for_patch (materialize the assess-path candidate) + BUG-6 single-tempdir ---


def test_diff_for_patch_disabled_does_no_io(tmp_path: Path) -> None:
    def should_not_index(root: Path) -> Path:
        raise AssertionError("indexer must not run when disabled")

    adapter = CodeGraphMaterializedDiffAdapter(enabled=False, indexer=should_not_index)
    result = adapter.diff_for_patch(repo_root=str(tmp_path), patch="diff --git a/x b/x\n")
    assert result.available is False
    assert result.fallback_reason == "materialized CodeGraph diff disabled"


def test_diff_for_patch_empty_patch_touches_nothing(tmp_path: Path) -> None:
    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=lambda r: r)
    assert adapter.diff_for_patch(repo_root=str(tmp_path), patch="").fallback_reason == (
        "no files touched by patch"
    )


def test_diff_for_patch_non_applying_patch_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("actual content\n", encoding="utf-8")
    bad = (
        "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n"
        "@@ -1 +1 @@\n-does not match\n+new\n"
    )
    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=lambda r: r)
    result = adapter.diff_for_patch(repo_root=str(tmp_path), patch=bad)
    assert result.available is False
    assert result.fallback_reason == "candidate patch did not apply cleanly to the current working tree"


def test_diff_for_patch_happy_path_reuses_one_tempdir(tmp_path: Path) -> None:
    # BUG-6 regression: before and after must be indexed in the SAME temp dir (identical path prefix),
    # not two separate random dirs.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export function f(x: number) { return x }\n", "utf-8")
    patch = (
        "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n"
        "@@ -1 +1 @@\n"
        "-export function f(x: number) { return x }\n"
        "+export function f(x: string) { return x }\n"
    )
    roots: list[str] = []

    def fake_index(root: Path) -> Path:
        roots.append(str(root))
        db_path = root / ".codegraph" / "codegraph.db"
        sig = "(x: number) => number" if len(roots) == 1 else "(x: string) => string"
        _make_db(db_path, [("src/a.ts", "f", sig, "public", "number")])
        return db_path

    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=fake_index)
    result = adapter.diff_for_patch(repo_root=str(tmp_path), patch=patch)
    assert len(roots) == 2 and roots[0] == roots[1]  # one reused tempdir, not two
    assert result.available is True
    assert result.rows[0].signature_changed is True


def test_diff_for_patch_rejects_path_traversal_header(tmp_path: Path) -> None:
    # An untrusted patch header with ../ must be rejected BEFORE reading repo_root -> no arbitrary read.
    def should_not_index(root: Path) -> Path:
        raise AssertionError("must not index an escaping patch")

    evil = (
        "diff --git a/../../../outside.txt b/../../../outside.txt\n"
        "--- a/../../../outside.txt\n+++ b/../../../outside.txt\n@@ -1 +1 @@\n-x\n+y\n"
    )
    adapter = CodeGraphMaterializedDiffAdapter(enabled=True, indexer=should_not_index)
    result = adapter.diff_for_patch(repo_root=str(tmp_path), patch=evil)
    assert result.available is False
    assert result.fallback_reason == "invalid patch file path"
