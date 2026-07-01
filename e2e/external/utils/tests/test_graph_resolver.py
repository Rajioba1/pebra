"""TDD unit tests for the CodeGraph diagnostic resolver (Phase 1 attribution, e2e-side only).

Real on-disk SQLite (no mocks) shaped like CodeGraph schema v5. These prove: the class/interface-level
``implements`` edge is the PRIMARY high-confidence proof (WorkspaceViewModel --implements--> IWorkspace);
graded fallback (located_symbol -> located_file -> symbol_name -> unresolved) with honest confidence; the
unresolved bucket is counted, never fabricated; and every failure path (missing db, schema < 5) fails
soft to ``unresolved`` instead of raising. No pebra import.
"""

from __future__ import annotations

import sqlite3

from e2e.external.utils import diagnostic_parser as dp
from e2e.external.utils import graph_resolver as gr

_BROKEN_FILE = "src/App/ViewModels/WorkspaceViewModel.cs"
_IFACE_FILE = "src/TemplateBlueprint.Core/Contracts/IWorkspace.cs"
_EDITED = "IWorkspace::CanCloseAsync"


def _make_db(tmp_path, *, version=5, with_implements=True, with_class=True, with_calls=False):
    db = tmp_path / "codegraph.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE schema_versions (version INTEGER);
        CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT,
                            file_path TEXT, start_line INTEGER, end_line INTEGER);
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT, provenance TEXT);
        """
    )
    con.execute("INSERT INTO schema_versions VALUES (?)", (version,))
    con.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
        ("iface", "interface", "IWorkspace", "TemplateBlueprint.Core.Contracts.IWorkspace",
         _IFACE_FILE, 3, 12),
    )
    con.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
        ("ifacem", "method", "CanCloseAsync", "IWorkspace.CanCloseAsync", _IFACE_FILE, 5, 5),
    )
    if with_class:
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
            ("cls", "class", "WorkspaceViewModel", "App.ViewModels.WorkspaceViewModel",
             _BROKEN_FILE, 5, 80),
        )
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
            ("clsm", "method", "CanCloseAsync", "App.ViewModels.WorkspaceViewModel.CanCloseAsync",
             _BROKEN_FILE, 40, 45),
        )
    if with_implements and with_class:
        con.execute("INSERT INTO edges VALUES (?,?,?,?)", ("cls", "iface", "implements", "ast"))
    if with_calls and with_class:
        con.execute("INSERT INTO edges VALUES (?,?,?,?)", ("ifacem", "clsm", "calls", "heuristic"))
    con.commit()
    con.close()
    return db


def _cs0535(line=9):
    msg = f"{_BROKEN_FILE.replace('/', chr(92))}({line},20): error CS0535: 'WorkspaceViewModel' does not implement interface member 'IWorkspace.CanCloseAsync()'"
    # parse using a repo_root that leaves the path already-relative (backslash normalized away)
    [d] = dp.parse_diagnostics(msg.replace(chr(92), "/"), "")
    return d


def test_resolve_located_symbol_with_implements_edge(tmp_path):
    db = _make_db(tmp_path)
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    assert r.attribution_method == "located_symbol+implements_edge"
    assert r.attribution_confidence == 1.0
    assert r.implements_edge is True
    assert r.edge_kind == "implements"
    assert r.interface_name == "IWorkspace"


def test_resolve_located_symbol_without_edge_when_no_implements(tmp_path):
    db = _make_db(tmp_path, with_implements=False)
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    assert r.attribution_method == "located_symbol"
    assert r.attribution_confidence == 0.9
    assert r.implements_edge is False
    assert r.edge_kind is None


def test_resolve_located_file_when_no_symbol_span(tmp_path):
    db = _make_db(tmp_path, with_class=False)  # file only present via the interface node? no: broken file absent
    # Put the broken file in the graph but with no span covering the diagnostic line, and no class node.
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
        ("stub", "field", "x", "x", _BROKEN_FILE, 1, 2),
    )
    con.commit()
    con.close()
    r = gr.resolve_diagnostic(_cs0535(line=9), _EDITED, db)
    assert r.attribution_method == "located_file"
    assert r.attribution_confidence == 0.7


def _db_class_in_other_file(tmp_path, *, with_implements):
    # broken CLASS is known by name, but its diagnostic file is NOT indexed at all -> symbol_name branch.
    db = tmp_path / "codegraph.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE schema_versions (version INTEGER);
        CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT,
                            file_path TEXT, start_line INTEGER, end_line INTEGER);
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT, provenance TEXT);
        """
    )
    con.execute("INSERT INTO schema_versions VALUES (5)")
    con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                ("iface", "interface", "IWorkspace", "IWorkspace", _IFACE_FILE, 3, 12))
    # class in a DIFFERENT file than the diagnostic's _BROKEN_FILE (so file_present is False)
    con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                ("cls", "class", "WorkspaceViewModel", "App.WorkspaceViewModel",
                 "src/Other/Elsewhere.cs", 5, 80))
    if with_implements:
        con.execute("INSERT INTO edges VALUES (?,?,?,?)", ("cls", "iface", "implements", "ast"))
    con.commit()
    con.close()
    return db


def test_resolve_symbol_name_fallback_when_file_not_indexed(tmp_path):
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, _db_class_in_other_file(tmp_path, with_implements=False))
    assert r.attribution_method == "symbol_name"
    assert r.attribution_confidence == 0.6
    assert r.implements_edge is False


def test_symbol_name_fallback_still_finds_implements_edge(tmp_path):
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, _db_class_in_other_file(tmp_path, with_implements=True))
    assert r.attribution_method == "symbol_name+implements_edge"
    assert r.attribution_confidence == 0.8
    assert r.implements_edge is True


def test_resolve_unresolved_when_file_not_in_graph(tmp_path):
    db = _make_db(tmp_path, with_class=False)  # broken file has no nodes at all
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    assert r.attribution_method == "unresolved"
    assert r.attribution_confidence == 0.0
    assert r.fallback_reason


def test_method_match_is_secondary_signal_when_calls_edge_present(tmp_path):
    db = _make_db(tmp_path, with_calls=True)
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    # primary proof is still the implements edge; method_match is an extra medium-confidence signal
    assert r.implements_edge is True
    assert r.method_match is True


def test_no_method_match_when_calls_edge_absent(tmp_path):
    db = _make_db(tmp_path, with_calls=False)
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    assert r.method_match is False


def test_unresolved_count_is_accurate(tmp_path):
    db = _make_db(tmp_path)
    resolvable = _cs0535()
    missing_a = dp.parse_diagnostics("src/Gone/A.cs(1,1): error CS0103: nope", "")[0]
    missing_b = dp.parse_diagnostics("src/Gone/B.cs(2,2): error CS0103: nope", "")[0]
    results, unresolved = gr.resolve_diagnostics([resolvable, missing_a, missing_b], _EDITED, db)
    assert len(results) == 3
    assert unresolved == 2


def test_missing_db_file_returns_unresolved(tmp_path):
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, tmp_path / "does_not_exist.db")
    assert r.attribution_method == "unresolved"
    assert r.attribution_confidence == 0.0
    assert r.graph_freshness == "unknown"


def test_schema_below_v5_returns_unresolved(tmp_path):
    db = _make_db(tmp_path, version=4)
    r = gr.resolve_diagnostic(_cs0535(), _EDITED, db)
    assert r.attribution_method == "unresolved"
    assert "schema" in (r.fallback_reason or "").lower()


def test_assemble_blob_has_required_shape(tmp_path):
    db = _make_db(tmp_path)
    results, unresolved = gr.resolve_diagnostics([_cs0535()], _EDITED, db)
    blob = gr.assemble_graph_attribution(
        results, diags=[_cs0535()], predicted_dependents=13, unresolved_count=unresolved
    )
    for key in (
        "error_kind", "diagnostic", "broken_file", "broken_line", "broken_symbol",
        "interface", "edited_symbol", "edge_kind", "implements_edge", "method_match",
        "predicted_callers", "actual_broken_files", "attribution_method",
        "attribution_confidence", "unresolved_count", "graph_freshness",
    ):
        assert key in blob, f"missing key {key!r}"
    assert blob["error_kind"] == "compiler"
    assert blob["predicted_callers"] == 13
    assert blob["actual_broken_files"] == 1
    assert blob["unresolved_count"] == 0
