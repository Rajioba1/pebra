"""`pebra dependents` — file-level blast radius via the codegraph reverse-edge query.

Returns the distinct repo-relative FILES whose symbols depend on (call/reference/instantiate/implement/
extend) any callable in a target file — the concrete file list a "blast-radius" safe-edit advisory needs
(not just fan-in counts). Tests build a tiny codegraph-shaped SQLite (schema v5) + inject a fake
``status_fn`` so the SQL runs without the codegraph binary. cli -> composition -> adapter; core untouched.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pebra import composition
from pebra.adapters import codegraph_adapter as cga
from pebra.cli import dependents as dep_cmd
from pebra.cli.main import build_parser

_FRESH = {"initialized": True,
          "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
          "index": {"reindexRecommended": False}, "version": "1.1.1"}


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at INTEGER, description TEXT);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT,
            file_path TEXT, language TEXT, start_line INTEGER, end_line INTEGER, updated_at INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, kind TEXT,
            provenance TEXT);
        """
    )
    con.execute("INSERT INTO schema_versions VALUES (5, 0, 's')")
    con.commit()
    con.close()


def _node(con, nid, kind, file_path):
    con.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
                "end_line, updated_at) VALUES (?,?,?,?,?,'csharp',1,9,0)", (nid, kind, nid, nid, file_path))


def _edge(con, src, tgt, kind="calls"):
    con.execute("INSERT INTO edges (source, target, kind, provenance) VALUES (?,?,?,'ts')", (src, tgt, kind))


def _seed(tmp_path: Path) -> cga.CodeGraphAdapter:
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    db = cg / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "t1", "method", "src/target.cs")     # the changed file's callable
    _node(con, "c1", "method", "src/a.cs")           # caller in another file
    _node(con, "c2", "method", "src/b.cs")           # caller in another file
    _node(con, "impl", "class", "src/c.cs")          # implementer (implements edge)
    _node(con, "self", "method", "src/target.cs")    # same-file caller (must be excluded)
    _edge(con, "c1", "t1", "calls")
    _edge(con, "c2", "t1", "references")
    _edge(con, "impl", "t1", "implements")
    _edge(con, "self", "t1", "calls")                # same file -> excluded
    con.commit()
    con.close()
    return cga.CodeGraphAdapter(status_fn=lambda r: _FRESH)


# ---- adapter SQL --------------------------------------------------------------------------------

def test_dependent_files_lists_distinct_source_files_excluding_self(tmp_path):
    adapter = _seed(tmp_path)
    deps = adapter.dependent_files("src/target.cs", str(tmp_path))
    assert deps == ["src/a.cs", "src/b.cs", "src/c.cs"]  # sorted, distinct, target file excluded


def test_dependent_files_empty_without_graph(tmp_path):
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: None)
    assert adapter.dependent_files("src/x.cs", str(tmp_path)) == []


def test_dependent_files_result_marks_absent_graph_unavailable(tmp_path):
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: None)
    result = adapter.dependent_files_result("src/x.cs", str(tmp_path))
    assert result["available"] is False
    assert result["dependent_files"] == []
    assert "codegraph" in result["fallback_reason"].lower()


def test_dependent_files_result_marks_stale_graph_unavailable(tmp_path):
    stale = {
        "initialized": True,
        "pendingChanges": {"added": 0, "modified": 1, "removed": 0},
        "index": {"reindexRecommended": False},
        "version": "1.1.1",
    }
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: stale)
    result = adapter.dependent_files_result("src/x.cs", str(tmp_path))
    assert result["available"] is False
    assert result["graph_freshness"] == "stale"
    assert "stale" in result["fallback_reason"].lower()


def test_dependent_files_empty_for_unreferenced_target(tmp_path):
    adapter = _seed(tmp_path)
    assert adapter.dependent_files("src/nobody_calls_me.cs", str(tmp_path)) == []


def test_dependent_files_same_file_only_caller_is_empty(tmp_path):
    # isolate the self-exclusion path: the target's ONLY caller is in the same file -> []
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    db = cg / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "t1", "method", "src/target.cs")
    _node(con, "self", "method", "src/target.cs")
    _edge(con, "self", "t1", "calls")
    con.commit()
    con.close()
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: _FRESH)
    assert adapter.dependent_files("src/target.cs", str(tmp_path)) == []


def test_dependent_files_excludes_non_blast_edge_kinds(tmp_path):
    # an 'imports' edge (module-level, NOT in _MODIFY_IMPACT_EDGE_KINDS) must not count as a dependent
    cg = tmp_path / ".codegraph"
    cg.mkdir()
    db = cg / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "t1", "method", "src/target.cs")
    _node(con, "imp", "method", "src/importer.cs")
    _edge(con, "imp", "t1", "imports")
    con.commit()
    con.close()
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: _FRESH)
    assert adapter.dependent_files("src/target.cs", str(tmp_path)) == []


def test_dependent_files_accepts_absolute_target(tmp_path):
    # _repo_relative must normalize an absolute --target to the stored repo-relative form.
    adapter = _seed(tmp_path)
    abs_target = str(tmp_path / "src" / "target.cs")
    assert adapter.dependent_files(abs_target, str(tmp_path)) == ["src/a.cs", "src/b.cs", "src/c.cs"]


# ---- composition + CLI --------------------------------------------------------------------------

def test_composition_dependent_files_empty_without_graph(tmp_path):
    assert composition.dependent_files(str(tmp_path), "src/x.cs") == []


def test_dependents_cli_registered_and_json_shape(monkeypatch, capsys):
    args = build_parser().parse_args(["dependents", "--target", "src/x.cs", "--json"])
    assert args.func is dep_cmd.run_dependents
    monkeypatch.setattr(composition, "dependent_files_result", lambda repo_root, target: {
        "available": True,
        "graph_freshness": "fresh",
        "dependent_files": ["src/a.cs", "src/b.cs"],
        "count": 2,
        "fallback_reason": None,
    })
    rc = dep_cmd.run_dependents(build_parser().parse_args(
        ["dependents", "--target", "src/x.cs", "--repo-root", "/r", "--json"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["command"] == "dependents" and out["target"] == "src/x.cs"
    assert out["available"] is True and out["graph_freshness"] == "fresh"
    assert out["dependent_files"] == ["src/a.cs", "src/b.cs"] and out["count"] == 2


def test_dependents_cli_zero_when_no_graph(tmp_path, capsys):
    rc = dep_cmd.run_dependents(build_parser().parse_args(
        ["dependents", "--target", "src/x.cs", "--repo-root", str(tmp_path), "--json"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["count"] == 0
    assert out["available"] is False
    assert out["fallback_reason"]
