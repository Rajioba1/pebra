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
         "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
         "version": "1.1.1"}
STALE = {"pendingChanges": {"added": 0, "modified": 1, "removed": 0},
         "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
         "version": "1.1.1"}


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
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_hot_subgraph_failsoft_when_graph_absent(tmp_path) -> None:
    # no .codegraph dir seeded, status_fn returns None -> CLI-not-found style absence
    out = gr.CodeGraphReader(status_fn=lambda r: None).hot_subgraph(["X"], str(tmp_path))
    assert out["available"] is False
    assert out["nodes"] == []
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_file_overview_uninitialized_graph_has_setup_guidance(tmp_path) -> None:
    out = gr.CodeGraphReader(
        status_fn=lambda _r: {"initialized": False, "version": "1.1.1"}
    ).file_overview(str(tmp_path))

    assert out["available"] is False
    assert "not initialized" in out["fallback_reason"]
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_file_overview_out_of_range_graph_has_setup_guidance(tmp_path) -> None:
    out = gr.CodeGraphReader(
        status_fn=lambda _r: {"initialized": True, "version": "9.0.0"}
    ).file_overview(str(tmp_path))

    assert out["available"] is False
    assert "outside accepted range" in out["fallback_reason"]
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_file_overview_missing_db_has_setup_guidance(tmp_path) -> None:
    out = gr.CodeGraphReader(status_fn=lambda _r: FRESH).file_overview(str(tmp_path))

    assert out["available"] is False
    assert "DB not found" in out["fallback_reason"]
    assert "pebra setup-graph --fix" in out["fallback_reason"]


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


# ---- god_node_map (M10b) -----------------------------------------------------


def test_top_symbols_in_file_ranks_by_structural_fanin_with_normalized_path(tmp_path) -> None:
    _seed(tmp_path)
    rows = _reader().top_symbols_in_file(str(tmp_path), "src\\Gamma.cs", limit=3)

    assert rows["available"] is True
    assert rows["file_path"] == "src/Gamma.cs"
    assert rows["symbols"][0]["id"] == "n:gamma"
    assert rows["symbols"][0]["qualified_name"] == "Gamma::Gamma"
    assert rows["symbols"][0]["inbound_count"] == 2
    assert rows["symbols"][0]["label"] == "Gamma"


def test_top_symbols_in_file_fanin_excludes_inheritance_edges(tmp_path) -> None:
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "n:child", "class", "Child", "Child", "src/Child.cs")
    _edge(con, "n:child", "n:gamma", "extends")
    con.commit()
    con.close()

    rows = _reader().top_symbols_in_file(str(tmp_path), "src/Gamma.cs", limit=1)

    assert rows["symbols"][0]["id"] == "n:gamma"
    assert rows["symbols"][0]["inbound_count"] == 2  # calls/references only, not extends


def test_god_node_map_uses_file_hubs_symbol_circles_spokes_and_cross_links(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().god_node_map(str(tmp_path), max_files=2, max_symbols_per_file=1, max_edges=10)

    assert out["available"] is True
    assert out["mode"] == "godmap"
    assert out["collapsed"] is False
    assert out["total_file_count"] == 2  # fan-in-bearing files only, matching file_overview
    assert out["total_symbol_count"] == 5
    hubs = [n for n in out["nodes"] if n["graph_role"] == "hub"]
    symbols = [n for n in out["nodes"] if n["graph_role"] == "symbol"]
    assert [h["id"] for h in hubs] == ["file:src/Gamma.cs", "file:src/A.cs"]
    assert all(h["kind"] == "file_hub" and h["shape"] == "rectangle" for h in hubs)
    assert [s["id"] for s in symbols] == ["n:gamma", "n:a"]
    assert all(s["shape"] == "ellipse" and s["qualified_name"] for s in symbols)
    assert any(
        {"source": "file:src/Gamma.cs", "target": "n:gamma", "kind": "contains",
         "edge_type": "spoke", "line_style": "dashed"}.items() <= e.items()
        for e in out["edges"]
    )
    assert any(
        {"source": "n:a", "target": "n:gamma", "kind": "calls",
         "edge_type": "cross_symbol", "line_style": "solid"}.items() <= e.items()
        for e in out["edges"]
    )
    assert out["truncated"] is False


def test_god_node_map_hub_symbol_count_includes_zero_fanin_symbols(tmp_path) -> None:
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "n:helper", "function", "Helper", "Helper", "src/Gamma.cs")
    con.commit()
    con.close()

    out = _reader().god_node_map(str(tmp_path), max_files=1, max_symbols_per_file=5)
    hub = next(n for n in out["nodes"] if n["graph_role"] == "hub")

    assert hub["id"] == "file:src/Gamma.cs"
    assert hub["symbol_count"] == 2


def test_god_node_map_reports_true_fanin_file_total_when_capped(tmp_path) -> None:
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    for i in range(4):
        _node(con, f"n:extra_src:{i}", "function", f"Src{i}", f"Src{i}", f"src/S{i}.cs")
        _node(con, f"n:extra_tgt:{i}", "function", f"Tgt{i}", f"Tgt{i}", f"src/T{i}.cs")
        _edge(con, f"n:extra_src:{i}", f"n:extra_tgt:{i}", "calls")
    con.commit()
    con.close()

    out = _reader().god_node_map(str(tmp_path), max_files=2, max_symbols_per_file=1)

    assert out["total_file_count"] == 6  # 2 original fan-in files + 4 extras, not max_files + 1
    assert out["total_node_count"] == 12  # all fan-in file hubs + all their structural symbols
    assert out["total_edge_count"] == 7  # all spokes plus complete selected-symbol cross-links
    assert out["truncated"] is True


def test_god_node_map_failsoft_and_caps_nodes(tmp_path) -> None:
    _seed(tmp_path)
    stale = _reader(STALE).god_node_map(str(tmp_path))
    assert stale["available"] is False
    assert stale["nodes"] == [] and stale["edges"] == []

    capped = _reader().god_node_map(str(tmp_path), max_files=2, max_symbols_per_file=3, max_nodes=1)
    assert capped["available"] is True
    assert [n["graph_role"] for n in capped["nodes"]] == ["hub"]
    assert capped["edges"] == []
    assert capped["truncated"] is True


def test_top_symbols_in_file_matches_backslash_paths_stored_in_db(tmp_path) -> None:
    _seed(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "n:win_src", "function", "WinSrc", "WinSrc", "src\\Caller.cs")
    _node(con, "n:win_tgt", "function", "WinTgt", "WinTgt", "src\\Win.cs")
    _edge(con, "n:win_src", "n:win_tgt", "calls")
    con.commit()
    con.close()

    out = _reader().top_symbols_in_file(str(tmp_path), "src/Win.cs")

    assert out["available"] is True
    assert out["symbols"][0]["id"] == "n:win_tgt"
    assert out["symbols"][0]["file_path"] == "src/Win.cs"


# ---- full_graph (M2) ---------------------------------------------------------


def test_full_graph_returns_symbol_nodes_and_edges_deterministically(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path))
    assert out["available"] is True
    assert out["mode"] == "symbol"
    assert out["collapsed"] is False
    ids = [n["id"] for n in out["nodes"]]
    assert ids == sorted(ids)  # deterministic ORDER BY id
    assert set(ids) == {"n:gamma", "n:a", "n:b", "n:c", "n:lonely"}
    assert out["total_node_count"] == 5
    assert out["total_edge_count"] == 3
    assert out["truncated"] is False
    # every structural edge is present and its endpoints are in the node set
    node_set = set(ids)
    for e in out["edges"]:
        assert e["source"] in node_set and e["target"] in node_set
        assert {"source", "target", "kind"} <= set(e)
    # deterministic across calls
    assert _reader().full_graph(str(tmp_path)) == out


def test_full_graph_node_carries_label_and_degree_fields(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path))
    by_id = {n["id"]: n for n in out["nodes"]}
    for n in out["nodes"]:
        assert {"id", "kind", "qualified_name", "file_path", "label",
                "degree", "inbound_count", "outbound_count"} <= set(n)
    gamma = by_id["n:gamma"]
    assert gamma["inbound_count"] == 2 and gamma["outbound_count"] == 0
    assert gamma["degree"] == 2
    assert gamma["label"]  # short, non-empty
    a = by_id["n:a"]
    assert a["inbound_count"] == 1 and a["outbound_count"] == 1 and a["degree"] == 2


def test_full_graph_caps_nodes_and_reports_true_total(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path), max_nodes=2)
    assert out["truncated"] is True
    assert len(out["nodes"]) == 2
    assert out["total_node_count"] == 5  # honest pre-cap count
    node_set = {n["id"] for n in out["nodes"]}
    for e in out["edges"]:  # no dangling edges to dropped nodes
        assert e["source"] in node_set and e["target"] in node_set


def test_full_graph_caps_edges(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path), max_edges=1)
    assert out["truncated"] is True
    assert len(out["edges"]) == 1


def test_full_graph_collapses_to_file_mode_above_threshold(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path), collapse_after=2)
    assert out["available"] is True
    assert out["mode"] == "file"
    assert out["collapsed"] is True
    assert out["total_node_count"] == 5  # true underlying symbol count
    assert out["total_file_count"] == 5
    ids = [n["id"] for n in out["nodes"]]
    assert ids == ["src/Gamma.cs", "src/A.cs", "src/B.cs", "src/C.cs", "src/L.cs"]
    node_set = set(ids)
    # one node per file; file edges aggregate symbol edges with a weight and never dangle
    assert len(out["nodes"]) == 5  # 5 files
    for n in out["nodes"]:
        assert n["kind"] == "file"
        assert n["file_path"] and "symbol_count" in n and "inbound_count" in n
    for e in out["edges"]:
        assert e["source"] in node_set and e["target"] in node_set
        assert e["weight"] >= 1


def test_full_graph_file_mode_edge_budget_not_starved_by_dropped_files(tmp_path) -> None:
    # File mode (5 files > collapse_after) AND the file-node cap keeps the hottest inbound files
    # first: Gamma.cs (2 callers), A.cs (1 caller), then B.cs (stable tie). The only kept->kept file
    # edge is A.cs -> Gamma.cs.
    # The edge budget must not be spent on pairs touching dropped files, or the real kept-kept edge
    # would be starved and edges would come back empty with a dishonest truncated flag.
    _seed(tmp_path)
    out = _reader().full_graph(str(tmp_path), collapse_after=2, max_nodes=3, max_edges=2)
    assert out["mode"] == "file"
    kept = {n["id"] for n in out["nodes"]}
    assert [n["id"] for n in out["nodes"]] == ["src/Gamma.cs", "src/A.cs", "src/B.cs"]
    assert kept == {"src/Gamma.cs", "src/A.cs", "src/B.cs"}
    assert {"source": "src/A.cs", "target": "src/Gamma.cs", "kind": "file_aggregate", "weight": 1} in out["edges"]
    for e in out["edges"]:
        assert e["source"] in kept and e["target"] in kept


def test_full_graph_failsoft_when_stale(tmp_path) -> None:
    _seed(tmp_path)
    out = _reader(STALE).full_graph(str(tmp_path))
    assert out["available"] is False
    assert out["graph_freshness"] == "stale"
    assert out["nodes"] == [] and out["edges"] == []
    assert out["mode"] == "symbol" and out["collapsed"] is False
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_full_graph_failsoft_when_absent(tmp_path) -> None:
    out = gr.CodeGraphReader(status_fn=lambda r: None).full_graph(str(tmp_path))
    assert out["available"] is False
    assert out["nodes"] == [] and out["edges"] == []
    assert "pebra setup-graph --fix" in out["fallback_reason"]


def test_full_graph_failsoft_on_corrupt_db(tmp_path) -> None:
    cg = tmp_path / ".codegraph"
    cg.mkdir(parents=True)
    con = sqlite3.connect(str(cg / "codegraph.db"))
    con.execute("CREATE TABLE junk (x)")
    con.commit()
    con.close()
    out = _reader().full_graph(str(tmp_path))
    assert out["available"] is False
    assert out["nodes"] == [] and out["edges"] == []
