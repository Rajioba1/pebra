"""codegraph_graph_reader — bulk node/edge reads for the dashboard graph view.

Builds a tiny codegraph-shaped DB (schema v5) and injects a fake ``status_fn`` so the SQL runs without
the binary — same fixture idiom as ``test_codegraph_adapter``. Proves the hotspot subgraph (BFS blast
radius around changed symbols), the whole-repo file overview, bounding/truncation, and fail-soft.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pebra.adapters import codegraph_graph_reader as gr

FRESH = {"pendingChanges": {"added": 0, "modified": 0, "removed": 0},
         "index": {"reindexRecommended": False}, "version": "1.1.1"}
STALE = {"pendingChanges": {"added": 0, "modified": 1, "removed": 0},
         "index": {"reindexRecommended": False}, "version": "1.1.1"}


def _make_db(path: Path, *, schema_version: int = 5) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at INTEGER, description TEXT);
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
            language TEXT, start_line INTEGER, end_line INTEGER, start_column INTEGER,
            end_column INTEGER, docstring TEXT, signature TEXT, visibility TEXT, is_exported INTEGER,
            is_async INTEGER, is_static INTEGER, is_abstract INTEGER, decorators TEXT,
            type_parameters TEXT, return_type TEXT, updated_at INTEGER);
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, target TEXT, kind TEXT,
            metadata TEXT, line INTEGER, col INTEGER, provenance TEXT);
        CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT, language TEXT, size INTEGER,
            modified_at INTEGER, indexed_at INTEGER, node_count INTEGER, errors TEXT);
        CREATE TABLE project_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER);
        """
    )
    con.execute("INSERT INTO schema_versions VALUES (?, 0, 's')", (schema_version,))
    con.commit()
    con.close()


def _node(con, nid, kind, name, qual, file_path, lo=1, hi=5):
    con.execute(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
        "end_line, start_column, end_column, updated_at) VALUES (?,?,?,?,?,'csharp',?,?,0,0,0)",
        (nid, kind, name, qual, file_path, lo, hi),
    )


def _edge(con, src, tgt, kind="calls"):
    con.execute("INSERT INTO edges (source, target, kind, provenance) VALUES (?,?,?,'t')", (src, tgt, kind))


def _seed(root: Path) -> None:
    """gamma (hot, 2 direct callers a,b) ; a is itself called by c (depth-2 of gamma) ; a lonely node."""
    cg = root / ".codegraph"
    cg.mkdir(parents=True)
    db = cg / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "n:gamma", "method", "Gamma", "Gamma::Gamma", "src/Gamma.cs", 10, 40)
    _node(con, "n:a", "method", "A", "Svc::A", "src/A.cs")
    _node(con, "n:b", "function", "B", "B", "src/B.cs")
    _node(con, "n:c", "function", "C", "C", "src/C.cs")
    _node(con, "n:lonely", "function", "Lonely", "Lonely", "src/L.cs")
    _edge(con, "n:a", "n:gamma", "calls")       # depth-1 caller of gamma
    _edge(con, "n:b", "n:gamma", "references")  # depth-1 caller of gamma
    _edge(con, "n:c", "n:a", "calls")           # depth-2 (caller of a caller)
    con.commit()
    con.close()


def _reader(status=FRESH):
    return gr.CodeGraphReader(status_fn=lambda repo_root: status)


def test_hot_subgraph_collects_direct_callers(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().hot_subgraph(["Gamma::Gamma"], str(tmp_path), max_depth=1)
    assert out["available"] is True
    ids = {n["id"]: n for n in out["nodes"]}
    assert ids["n:gamma"]["depth"] == 0
    assert ids["n:a"]["depth"] == 1 and ids["n:b"]["depth"] == 1
    assert "n:c" not in ids          # depth-2, excluded at max_depth=1
    assert "n:lonely" not in ids     # unrelated
    assert {"source": "n:a", "target": "n:gamma", "kind": "calls"} in out["edges"]


def test_hot_subgraph_bfs_reaches_transitive_callers(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().hot_subgraph(["Gamma::Gamma"], str(tmp_path), max_depth=2)
    ids = {n["id"]: n for n in out["nodes"]}
    assert ids["n:c"]["depth"] == 2  # caller-of-a-caller reached at depth 2


def test_hot_subgraph_truncates_at_max_nodes(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().hot_subgraph(["Gamma::Gamma"], str(tmp_path), max_depth=2, max_nodes=2)
    assert out["truncated"] is True
    assert len(out["nodes"]) <= 2


def test_hot_subgraph_caps_many_center_symbols(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().hot_subgraph(
        ["Gamma::Gamma", "Svc::A", "B", "C"], str(tmp_path), max_depth=1, max_nodes=2
    )
    assert out["truncated"] is True
    assert len(out["nodes"]) == 2
    assert len(out["edges"]) <= 1


def test_hot_subgraph_resolves_qualified_name_with_file_path(tmp_path) -> None:
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "n:gamma-other", "method", "Gamma", "Gamma::Gamma", "src/Other.cs", 10, 40)
    con.commit()
    con.close()

    out = _reader().hot_subgraph(
        [{"qualified_name": "Gamma::Gamma", "file_path": "src/Gamma.cs"}], str(tmp_path), max_depth=1
    )
    ids = {n["id"] for n in out["nodes"]}
    assert "n:gamma" in ids
    assert "n:gamma-other" not in ids


def test_hot_subgraph_unknown_symbol_is_available_but_empty(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().hot_subgraph(["Nope::Gone"], str(tmp_path))
    assert out["available"] is True   # graph read fine
    assert out["nodes"] == []         # the symbol just isn't there (e.g. renamed)
    assert out["fallback_reason"]


def test_hot_subgraph_failsoft_when_graph_stale(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader(STALE).hot_subgraph(["Gamma::Gamma"], str(tmp_path))
    assert out["available"] is False
    assert out["graph_freshness"] == "stale"
    assert out["nodes"] == [] and out["edges"] == []


def test_hot_subgraph_failsoft_when_graph_absent(tmp_path) -> None:
    # no .codegraph dir seeded, status_fn returns None -> CLI-not-found style absence
    out = gr.CodeGraphReader(status_fn=lambda r: None).hot_subgraph(["X"], str(tmp_path))
    assert out["available"] is False
    assert out["nodes"] == []


def test_hot_subgraph_failsoft_on_corrupt_db(tmp_path) -> None:
    # A DB that exists but lacks the codegraph schema (corrupt/half-written/old era) must fail soft,
    # not raise: _schema_version's SELECT would otherwise throw straight out of the un-try'd gate.
    cg = tmp_path / ".codegraph"
    cg.mkdir(parents=True)
    db = cg / "codegraph.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE junk (x)")  # no schema_versions/nodes/edges tables
    con.commit()
    con.close()
    out = _reader().hot_subgraph(["X"], str(tmp_path))
    assert out["available"] is False
    assert out["nodes"] == [] and out["edges"] == []


def test_file_overview_failsoft_on_corrupt_db(tmp_path) -> None:
    cg = tmp_path / ".codegraph"
    cg.mkdir(parents=True)
    con = sqlite3.connect(str(cg / "codegraph.db"))
    con.execute("CREATE TABLE junk (x)")
    con.commit()
    con.close()
    out = _reader().file_overview(str(tmp_path))
    assert out["available"] is False
    assert out["files"] == []


def test_file_overview_ranks_hottest_files(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().file_overview(str(tmp_path))
    assert out["available"] is True
    files = {f["file_path"]: f for f in out["files"]}
    # Gamma.cs has 2 distinct callers into it; A.cs has 1; the rest 0 (no fan-in) are omitted.
    assert files["src/Gamma.cs"]["distinct_caller_count"] == 2
    assert files["src/A.cs"]["distinct_caller_count"] == 1
    assert out["files"][0]["file_path"] == "src/Gamma.cs"  # hottest first


def test_file_overview_respects_top_n(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().file_overview(str(tmp_path), top_n=1)
    assert len(out["files"]) == 1
    assert out["truncated"] is True
