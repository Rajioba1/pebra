"""Destructive-op slice, Phase 4 — CodeGraphAdapter.file_fanin_rollup (whole-file fan-in aggregate).

Pure SQLite fixture (no subprocess); status_fn injected. Verifies the union of distinct callers across
ALL callable symbols in a file, the worst single symbol, and fail-soft 'unresolved' on stale/no-DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pebra.adapters import codegraph_adapter as cga

FRESH = {"pendingChanges": {"added": 0, "modified": 0, "removed": 0},
         "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
         "worktreeMismatch": None,
         "initialized": True, "version": "1.1.1"}
STALE = {"pendingChanges": {"added": 0, "modified": 1, "removed": 0},
         "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
         "worktreeMismatch": None,
         "initialized": True, "version": "1.1.1"}


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at INTEGER, description TEXT);
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT,
            file_path TEXT, language TEXT, start_line INTEGER, end_line INTEGER, start_column INTEGER,
            end_column INTEGER, signature TEXT, updated_at INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, kind TEXT,
            metadata TEXT, line INTEGER, col INTEGER, provenance TEXT);
        CREATE TABLE project_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER);
        """
    )
    con.execute("INSERT INTO schema_versions VALUES (5, 0, 's')")
    con.execute("INSERT INTO project_metadata VALUES ('indexed_with_version', '1.1.1', 0)")
    con.execute("INSERT INTO project_metadata VALUES ('indexed_with_extraction_version', '24', 0)")
    con.commit()
    con.close()


def _node(con, nid, kind, name, qual, fp, lo, hi):
    con.execute(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
        "end_line, start_column, end_column, updated_at) VALUES (?,?,?,?,?,'python',?,?,0,0,0)",
        (nid, kind, name, qual, fp, lo, hi),
    )


def _edge(con, src, tgt, kind="calls"):
    con.execute("INSERT INTO edges (source, target, kind, provenance) VALUES (?,?,?,'ts')",
                (src, tgt, kind))


def _seed(root: Path) -> None:
    cg = root / ".codegraph"
    cg.mkdir(parents=True)
    db = cg / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    # src/auth.py: class LM (5-40) + method validate_login (10-20). filler callables for distribution.
    _node(con, "class:LM", "class", "LoginManager", "LoginManager", "src/auth.py", 5, 40)
    _node(con, "method:vl", "method", "validate_login", "LM::validate_login", "src/auth.py", 10, 20)
    _node(con, "func:helper", "function", "helper", "helper", "src/util.py", 1, 4)
    for i in range(5):
        _node(con, f"func:f{i}", "function", f"f{i}", f"f{i}", f"src/m{i}.py", 1, 3)
    # validate_login has 3 distinct callers (calls/references/instantiates count; imports does NOT)
    _edge(con, "func:f0", "method:vl", "calls")
    _edge(con, "func:f1", "method:vl", "references")
    _edge(con, "class:LM", "method:vl", "calls")
    _edge(con, "func:helper", "method:vl", "imports")  # excluded
    for i in range(5):
        _edge(con, "method:vl", f"func:f{i}", "calls")  # each filler gets 1 caller
    con.commit()
    con.close()


def _adapter(status=FRESH):
    return cga.CodeGraphAdapter(status_fn=lambda repo_root: status)


def test_rollup_unions_callers_across_all_symbols_in_file(tmp_path):
    _seed(tmp_path)
    r = _adapter().file_fanin_rollup("src/auth.py", str(tmp_path))
    assert r.resolution_method == "file_location"
    assert r.distinct_caller_count == 3   # {f0, f1, LM}; imports edge excluded
    assert r.max_caller_count == 3        # validate_login is the worst single symbol
    assert r.symbol_count == 2            # class + method
    assert r.file_symbol_fanin_rollup_percentile == pytest.approx(1.0)


def test_rollup_percentile_uses_file_union_distribution_not_symbol_distribution(tmp_path):
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "method:big", "method", "big", "Big::big", "src/big.py", 1, 10)
    for i in range(10):
        _node(con, f"func:big_caller_{i}", "function", f"bc{i}", f"bc{i}",
              "src/big_callers.py", i + 1, i + 1)
        _edge(con, f"func:big_caller_{i}", "method:big", "calls")
    con.commit()
    con.close()

    r = _adapter().file_fanin_rollup("src/auth.py", str(tmp_path))

    assert r.distinct_caller_count == 3
    assert r.file_symbol_fanin_rollup_percentile == pytest.approx(8 / 9)


def test_rollup_unresolved_when_stale(tmp_path):
    _seed(tmp_path)
    assert _adapter(status=STALE).file_fanin_rollup("src/auth.py", str(tmp_path)).resolution_method == "unresolved"


def test_rollup_unresolved_when_no_db(tmp_path):
    # no .codegraph dir at all
    assert _adapter().file_fanin_rollup("src/auth.py", str(tmp_path)).resolution_method == "unresolved"


def test_rollup_unresolved_when_status_none(tmp_path):
    _seed(tmp_path)
    adapter = cga.CodeGraphAdapter(status_fn=lambda r: None)
    assert adapter.file_fanin_rollup("src/auth.py", str(tmp_path)).resolution_method == "unresolved"


def test_rollup_file_with_no_callables_is_resolved_but_empty(tmp_path):
    _seed(tmp_path)
    r = _adapter().file_fanin_rollup("src/nonexistent.py", str(tmp_path))
    assert r.resolution_method == "file_location"
    assert r.symbol_count == 0
    assert r.distinct_caller_count == 0
