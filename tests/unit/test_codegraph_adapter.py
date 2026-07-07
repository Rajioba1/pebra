"""M5c.5 — codegraph_adapter: location-first per-symbol fan-in over a real (fixture) SQLite DB.

These tests build a tiny codegraph-shaped DB (schema subset, version 5) on disk and inject a fake
``status_fn`` so the full SQL + diff-parse + percentile math runs WITHOUT the codegraph binary. The
real ``codegraph status/sync`` subprocess path is covered separately behind ``requires_codegraph``.
"""

from __future__ import annotations

import dataclasses
import json as _json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from pebra.adapters import codegraph_adapter as cga
from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core import models as m
from pebra.core.constants import Decision
from pebra.core.models import CandidateAction
from tests.unit.test_assessment_builder import _worked_example_input

# NB: the real `codegraph status --json` emits INTEGER counts for pendingChanges (changes.*.length),
# not file lists (references/codegraph/src/bin/codegraph.ts:818-822) — fixtures mirror that shape.
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
    con.execute("INSERT INTO project_metadata VALUES ('indexed_with_version', '1.1.1', 0)")
    con.execute("INSERT INTO project_metadata VALUES ('indexed_with_extraction_version', '24', 0)")
    con.commit()
    con.close()


def _node(con, nid, kind, name, qual, file_path, lo, hi, col=0):
    con.execute(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
        "end_line, start_column, end_column, updated_at) VALUES (?,?,?,?,?,'python',?,?,?,0,0)",
        (nid, kind, name, qual, file_path, lo, hi, col),
    )


def _edge(con, src, tgt, kind="calls", provenance="tree-sitter"):
    con.execute(
        "INSERT INTO edges (source, target, kind, provenance) VALUES (?,?,?,?)",
        (src, tgt, kind, provenance),
    )


def _file(con, path, *, size=1000, node_count=1, errors=""):
    con.execute(
        "INSERT INTO files (path, content_hash, language, size, modified_at, indexed_at, "
        "node_count, errors) VALUES (?, 'h', 'csharp', ?, 0, 0, ?, ?)",
        (path, size, node_count, errors),
    )


def _seed_repo(root: Path) -> Path:
    """A small graph: validate_login (in auth.py, lines 10-20) inside class LoginManager (5-40),
    called by 3 callers; a lonely helper called by 0; plus filler callables so percentile is meaningful."""
    cg_dir = root / ".codegraph"
    cg_dir.mkdir(parents=True)
    db = cg_dir / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "class:LM", "class", "LoginManager", "LoginManager", "src/auth.py", 5, 40)
    _node(con, "method:vl", "method", "validate_login", "LoginManager::validate_login", "src/auth.py", 10, 20)
    _node(con, "func:helper", "function", "helper", "helper", "src/util.py", 1, 4)
    # filler callables to populate the distribution (5 more, each with 1 caller)
    for i in range(5):
        _node(con, f"func:f{i}", "function", f"f{i}", f"f{i}", f"src/m{i}.py", 1, 3)
    # 3 callers into validate_login (calls/references/instantiates all count)
    _edge(con, "func:f0", "method:vl", "calls")
    _edge(con, "func:f1", "method:vl", "references")
    _edge(con, "class:LM", "method:vl", "calls")
    # each filler gets exactly 1 caller, helper gets 0
    for i in range(5):
        _edge(con, "method:vl", f"func:f{i}", "calls")
    # an imports edge that must NOT count toward symbol fan-in
    _edge(con, "func:helper", "method:vl", "imports")
    con.commit()
    con.close()
    return db


def _adapter(status=FRESH):
    return cga.CodeGraphAdapter(status_fn=lambda repo_root: status)


def test_node_counts_counts_callable_and_csharp(tmp_path):
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir(parents=True)
    db = cg_dir / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    _node(con, "cls:A", "class", "A", "A", "src/A.cs", 1, 9)          # C# callable
    _node(con, "iface:I", "interface", "I", "I", "src/I.cs", 1, 5)    # C# callable
    _node(con, "fn:p", "function", "p", "p", "src/p.py", 1, 3)        # callable, not C#
    _node(con, "fld:x", "field", "x", "A::x", "src/A.cs", 2, 2)       # C#, NOT callable
    con.commit()
    con.close()
    counts = _adapter().node_counts(str(tmp_path))
    assert counts == {"total": 4, "callable": 3, "csharp_callable": 2}


def test_probe_capabilities_measures_per_language_coverage(tmp_path):
    from pebra.core.language_capability import classify_tier

    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir(parents=True)
    db = cg_dir / "codegraph.db"
    _make_db(db)
    con = sqlite3.connect(str(db))
    # typescript: 2 callables, both with signature + visibility -> FULL
    con.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
                "end_line, signature, visibility, updated_at) VALUES "
                "('ts1','function','a','a','a.ts','typescript',1,5,'(x:int)=>int','public',0)")
    con.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
                "end_line, signature, visibility, updated_at) VALUES "
                "('ts2','method','b','b','a.ts','typescript',6,9,'()=>void','public',0)")
    # csharp: 1 callable, visibility but NO signature -> PARTIAL (the real C# shape)
    con.execute("INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
                "end_line, signature, visibility, updated_at) VALUES "
                "('cs1','class','C','C','C.cs','csharp',1,9,NULL,'public',0)")
    con.execute("INSERT INTO edges (source, target, kind, provenance) VALUES ('ts1','ts2','calls','t')")
    con.commit()
    con.close()

    caps = _adapter().probe_capabilities(str(tmp_path))
    assert caps["typescript"].probe_status == "measured"
    assert caps["typescript"].node_count == 2
    assert caps["typescript"].signature_coverage_ratio == pytest.approx(1.0)
    assert caps["typescript"].visibility_coverage_ratio == pytest.approx(1.0)
    assert "calls" in caps["typescript"].edge_kinds
    assert classify_tier(caps["typescript"]) == "full"
    assert caps["csharp"].signature_coverage_ratio == pytest.approx(0.0)
    assert caps["csharp"].visibility_coverage_ratio == pytest.approx(1.0)
    assert classify_tier(caps["csharp"]) == "partial"


def test_structural_symbols_resolves_owner_from_after_diff(tmp_path):
    # verify-path (post-edit) resolution: a change at after-line 15 lands inside validate_login (10-20),
    # resolved against the current graph from before/after text alone (no patch string).
    _seed_repo(tmp_path)
    before = "\n".join(f"line{i}" for i in range(1, 41))
    after = before.replace("line15", "line15_changed")
    ev = _adapter().structural_symbols("src/auth.py", before, after, str(tmp_path))
    assert ev.resolution_method == "location"
    assert ev.node_ids_resolved == ("method:vl",)
    assert ev.resolved_qualified_names == ("LoginManager::validate_login",)
    assert ev.symbol_caller_count == 3  # same union fan-in the assess path sees for this owner


def test_structural_symbols_deleted_file_abstains(tmp_path):
    _seed_repo(tmp_path)
    ev = _adapter().structural_symbols("src/auth.py", "whatever", None, str(tmp_path))
    assert ev.resolution_method == "unresolved"  # no post-edit content -> honest abstain, never raises


def test_structural_symbols_deleted_file_checks_graph_freshness_first(tmp_path):
    _seed_repo(tmp_path)
    ev = cga.CodeGraphAdapter(status_fn=lambda r: None).structural_symbols(
        "src/auth.py", "whatever", None, str(tmp_path)
    )
    assert ev.graph_freshness == "unknown"
    assert "CLI not found" in (ev.fallback_reason or "")


def test_capability_for_graph_unavailable_is_not_measured(tmp_path):
    cap = cga.CodeGraphAdapter(status_fn=lambda r: None).capability_for("python", str(tmp_path))
    assert cap.probe_status == "graph_unavailable"
    assert cap.fallback_reason  # honest reason, never a fabricated 'measured'


def test_capability_for_indexed_language_absent_is_measured_zero(tmp_path):
    _seed_repo(tmp_path)  # python-only fixture
    cap = _adapter().capability_for("rust", str(tmp_path))
    assert cap.probe_status == "measured" and cap.node_count == 0  # graph read, just no rust nodes


def test_probe_rejects_worktree_mismatch(tmp_path):
    # capability is a TRUST claim: a borrowed/foreign-worktree index must NOT be reported as measured
    _seed_repo(tmp_path)
    status = {**FRESH, "worktreeMismatch": True}
    cap = cga.CodeGraphAdapter(status_fn=lambda r: status).capability_for("python", str(tmp_path))
    assert cap.probe_status == "graph_unavailable"


def test_probe_rejects_out_of_range_codegraph_version(tmp_path):
    # an incompatible codegraph build may populate signature/visibility differently -> not trusted
    _seed_repo(tmp_path)
    status = {**FRESH, "version": "0.0.1"}
    cap = cga.CodeGraphAdapter(status_fn=lambda r: status).capability_for("python", str(tmp_path))
    assert cap.probe_status == "graph_unavailable"


def test_probe_is_memoized_per_repo_root(tmp_path):
    # fanin already spawns a status subprocess per action; the probe must not double it -> status_fn
    # is called at most once across repeated capability_for calls for the same repo_root.
    _seed_repo(tmp_path)
    calls = []

    def counting_status(r):
        calls.append(r)
        return FRESH

    adapter = cga.CodeGraphAdapter(status_fn=counting_status)
    adapter.capability_for("python", str(tmp_path))
    adapter.capability_for("rust", str(tmp_path))
    assert len(calls) == 1  # second call served from the per-repo_root probe cache


def test_node_counts_zero_when_graph_absent(tmp_path):
    # no codegraph status (CLI/index absent) -> honest zeros, never fabricated
    counts = cga.CodeGraphAdapter(status_fn=lambda r: None).node_counts(str(tmp_path))
    assert counts == {"total": 0, "callable": 0, "csharp_callable": 0}


def test_node_counts_zero_when_schema_below_v5(tmp_path):
    # a pre-v5 index must fail-soft to honest zeros (mirror of fanin's schema guard) rather than run
    # COUNT() against an old/foreign layout and report misleading numbers to the graph preflight.
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir(parents=True)
    _make_db(cg_dir / "codegraph.db", schema_version=4)
    counts = _adapter().node_counts(str(tmp_path))
    assert counts == {"total": 0, "callable": 0, "csharp_callable": 0}


def _assert_empty_graph_context(ev) -> None:
    assert ev.owner_kinds == ()
    assert ev.max_owner_span_lines == 0
    assert ev.resolved_symbol_count == 0
    assert ev.incoming_edge_counts == {}
    assert ev.outgoing_edge_counts == {}
    assert ev.modify_impact_count == 0
    assert ev.modify_impact_percentile == 0.0
    assert ev.modify_impact_edge_counts == {}
    assert ev.container_hierarchy_kinds == ()
    assert ev.graph_file_size_bytes == 0
    assert ev.graph_file_node_count == 0
    assert ev.graph_file_error_count == 0
    assert ev.contract_surface_kind == "unknown"
    assert ev.is_exported_contract is False
    assert ev.is_abstract_or_interface_contract is False
    assert ev.has_signature_metadata is False


_PATCH = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -12,3 +12,4 @@ class LoginManager:\n"
    "     def validate_login(self):\n"
    "-        return False\n"
    "+        return True\n"
)


# --- the unified-diff old-side hunk parser (pure) ---

def test_parse_old_side_ranges_collects_only_changed_old_lines() -> None:
    # the hunk header spans 12-14, but only line 13 is actually removed ('-'); context must NOT count
    ranges = cga.parse_old_side_ranges(_PATCH)
    assert ranges == {"src/auth.py": [(13, 13)]}


def test_parse_old_side_ranges_merges_contiguous_changed_lines() -> None:
    patch = ("--- a/x.py\n+++ b/x.py\n@@ -10,5 +10,5 @@\n ctx\n-a\n-b\n-c\n ctx2\n+new\n")
    # removed old lines 11,12,13 -> merged to one (11,13) range
    assert cga.parse_old_side_ranges(patch) == {"x.py": [(11, 13)]}


def test_parse_old_side_ranges_pure_addition_is_point() -> None:
    patch = "--- a/src/new.py\n+++ b/src/new.py\n@@ -42,0 +43,5 @@\n+new line\n"
    assert cga.parse_old_side_ranges(patch) == {"src/new.py": [(42, 42)]}


def test_parse_old_side_ranges_default_count_is_one() -> None:
    patch = "--- a/x.py\n+++ b/x.py\n@@ -7 +7 @@\n-a\n+b\n"
    assert cga.parse_old_side_ranges(patch) == {"x.py": [(7, 7)]}


def test_parse_old_side_ranges_skips_dev_null() -> None:
    patch = "--- /dev/null\n+++ b/added.py\n@@ -0,0 +1,3 @@\n+x\n"
    assert cga.parse_old_side_ranges(patch) == {}


def test_parse_old_side_ranges_non_diff_is_empty() -> None:
    assert cga.parse_old_side_ranges("just some prose, not a diff") == {}


# --- location-first resolution + fan-in ---

def test_location_resolves_tightest_owner_and_counts_callers(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="patch", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "location"
    assert ev.graph_freshness == "fresh"
    assert ev.node_ids_resolved == ("method:vl",)  # method, not the enclosing class
    assert ev.symbol_caller_count == 3  # calls + references + the LM call; imports excluded
    # distribution: validate_login=3, five fillers=1 each, helper=0, class=0 -> 8 callable nodes
    # fractional_rank(3, sorted[0,0,1,1,1,1,1,3]) = 8/8 = 1.0
    assert ev.symbol_fan_in_percentile == pytest.approx(1.0)
    assert ev.provider_version == "1.1.1"
    assert ev.index_version == "24"
    assert ev.owner_kinds == ("method",)
    assert ev.max_owner_span_lines == 11
    assert ev.resolved_symbol_count == 1
    assert ev.incoming_edge_counts == {"calls": 2, "references": 1, "imports": 1}
    assert ev.outgoing_edge_counts == {"calls": 5}
    # multi-language attach facts: the resolved owner's own language + graph-side qualified name
    assert ev.resolved_language == "python"
    assert ev.resolved_qualified_names == ("LoginManager::validate_login",)


def test_hunk_spanning_two_symbols_aggregates_graph_context(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "func:A", "function", "A", "A", "src/multi.py", 10, 25)
    _node(con, "func:B", "method", "B", "B", "src/multi.py", 30, 90)
    _node(con, "src:1", "function", "c1", "c1", "src/x.py", 1, 2)
    _node(con, "src:2", "function", "c2", "c2", "src/y.py", 1, 2)
    _edge(con, "src:1", "func:A", "calls")
    _edge(con, "src:2", "func:B", "references")
    _edge(con, "func:A", "src:2", "calls")
    _edge(con, "func:B", "src:1", "instantiates")
    con.commit()
    con.close()
    patch = ("--- a/src/multi.py\n+++ b/src/multi.py\n"
             "@@ -12 +12 @@\n-x\n+y\n"
             "@@ -35 +35 @@\n-p\n+q\n")

    ev = _adapter().fanin(CandidateAction(id="a1", label="p", action_type="edit",
                                          proposed_patch=patch), str(tmp_path))

    assert set(ev.node_ids_resolved) == {"func:A", "func:B"}
    assert ev.owner_kinds == ("function", "method")
    assert ev.max_owner_span_lines == 61
    assert ev.resolved_symbol_count == 2
    assert ev.incoming_edge_counts == {"calls": 1, "references": 1}
    assert ev.outgoing_edge_counts == {"calls": 1, "instantiates": 1}


def test_modify_impact_count_dedupes_callers_implementers_and_subclasses(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "method:iface", "method", "CanCloseAsync", "IWorkspace::CanCloseAsync",
          "src/IWorkspace.cs", 10, 15)
    _node(con, "class:caller_and_impl", "class", "WorkspaceViewModel", "WorkspaceViewModel",
          "src/WorkspaceViewModel.cs", 1, 120)
    _node(con, "class:sub", "class", "DerivedWorkspace", "DerivedWorkspace", "src/Derived.cs", 1, 80)
    _node(con, "func:other", "function", "Other", "Other", "src/Other.cs", 1, 5)
    _edge(con, "class:caller_and_impl", "method:iface", "calls")
    _edge(con, "class:caller_and_impl", "method:iface", "implements")
    _edge(con, "class:sub", "method:iface", "extends")
    _edge(con, "func:other", "class:sub", "calls")
    con.commit()
    con.close()
    patch = "--- a/src/IWorkspace.cs\n+++ b/src/IWorkspace.cs\n@@ -12 +12 @@\n-x\n+y\n"

    ev = _adapter().fanin(
        CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch),
        str(tmp_path),
    )

    assert ev.symbol_caller_count == 1
    assert ev.modify_impact_count == 2
    assert ev.modify_impact_percentile == pytest.approx(1.0)
    assert ev.modify_impact_edge_counts == {"calls": 1, "extends": 1, "implements": 1}


def test_modify_impact_includes_contract_container_edges_for_method_edits(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "interface:I", "interface", "IWorkspace", "IWorkspace", "src/IWorkspace.cs", 1, 30)
    _node(con, "method:close", "method", "CanCloseAsync", "IWorkspace::CanCloseAsync",
          "src/IWorkspace.cs", 28, 28)
    _node(con, "class:impl", "class", "WorkspaceViewModel", "WorkspaceViewModel",
          "src/WorkspaceViewModel.cs", 1, 120)
    _node(con, "class:impl2", "class", "OtherWorkspace", "OtherWorkspace", "src/Other.cs", 1, 90)
    _node(con, "func:caller", "function", "caller", "caller", "src/Caller.cs", 1, 10)
    _edge(con, "interface:I", "method:close", "contains")
    _edge(con, "class:impl", "interface:I", "implements")
    _edge(con, "class:impl", "method:close", "calls")
    _edge(con, "class:impl2", "interface:I", "implements")
    _edge(con, "func:caller", "interface:I", "references")
    con.commit()
    con.close()
    patch = "--- a/src/IWorkspace.cs\n+++ b/src/IWorkspace.cs\n@@ -28 +28 @@\n-x\n+y\n"

    ev = _adapter().fanin(
        CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch),
        str(tmp_path),
    )

    assert ev.symbol_caller_count == 1
    assert ev.modify_impact_count == 3
    assert ev.modify_impact_edge_counts == {"calls": 1, "implements": 2, "references": 1}


def test_contract_metadata_uses_interface_container_for_method_edits(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "interface:I", "interface", "IWorkspace", "IWorkspace", "src/IWorkspace.cs", 1, 30)
    _node(con, "method:close", "method", "CanCloseAsync", "IWorkspace::CanCloseAsync",
          "src/IWorkspace.cs", 28, 28)
    con.execute(
        "UPDATE nodes SET return_type = 'Task', signature = 'Task<bool> CanCloseAsync()' "
        "WHERE id = 'method:close'"
    )
    _edge(con, "interface:I", "method:close", "contains")
    con.commit()
    con.close()
    patch = "--- a/src/IWorkspace.cs\n+++ b/src/IWorkspace.cs\n@@ -28 +28 @@\n-x\n+y\n"

    ev = _adapter().fanin(
        CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch),
        str(tmp_path),
    )

    assert ev.contract_surface_kind == "interface_method"
    assert ev.is_abstract_or_interface_contract is True
    assert ev.is_exported_contract is True
    assert ev.has_signature_metadata is True


def test_modify_impact_rolls_up_full_container_hierarchy(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "namespace:core", "namespace", "Core", "Core", "src/Core.cs", 1, 200)
    _node(con, "class:vm", "class", "WorkspaceViewModel", "Core::WorkspaceViewModel",
          "src/Core.cs", 10, 180)
    _node(con, "method:close", "method", "CanCloseAsync",
          "Core::WorkspaceViewModel::CanCloseAsync", "src/Core.cs", 80, 90)
    _node(con, "func:caller", "function", "Caller", "Caller", "src/Caller.cs", 1, 5)
    _edge(con, "namespace:core", "class:vm", "contains")
    _edge(con, "class:vm", "method:close", "contains")
    _edge(con, "func:caller", "namespace:core", "references")
    con.commit()
    con.close()
    patch = "--- a/src/Core.cs\n+++ b/src/Core.cs\n@@ -84 +84 @@\n-x\n+y\n"

    ev = _adapter().fanin(
        CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch),
        str(tmp_path),
    )

    assert ev.symbol_caller_count == 0
    assert ev.modify_impact_count == 1
    assert ev.modify_impact_edge_counts == {"references": 1}
    assert ev.container_hierarchy_kinds == ("class", "namespace")


def test_graph_context_surfaces_file_metadata_and_parse_errors(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "method:close", "method", "CanCloseAsync", "CanCloseAsync", "src/Core.cs", 80, 90)
    _file(con, "src/Core.cs", size=240_000, node_count=750, errors='["parse error"]')
    con.commit()
    con.close()
    patch = "--- a/src/Core.cs\n+++ b/src/Core.cs\n@@ -84 +84 @@\n-x\n+y\n"

    ev = _adapter().fanin(
        CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch),
        str(tmp_path),
    )

    assert ev.graph_file_size_bytes == 240_000
    assert ev.graph_file_node_count == 750
    assert ev.graph_file_error_count == 1


def test_imports_edges_excluded_from_fanin(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.symbol_caller_count == 3  # the 'imports' edge into method:vl is NOT counted


def test_stale_index_fails_closed(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=STALE).fanin(action, str(tmp_path))
    assert ev.graph_freshness == "stale"
    assert ev.resolution_method == "unresolved"
    assert ev.symbol_fan_in_percentile == 0.0
    assert ev.fallback_reason and "stale" in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


def test_reindex_recommended_is_stale(tmp_path) -> None:
    _seed_repo(tmp_path)
    status = {"pendingChanges": {"added": 0, "modified": 0, "removed": 0},
              "index": {"reindexRecommended": True}}
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=status).fanin(action, str(tmp_path))
    assert ev.graph_freshness == "stale" and ev.resolution_method == "unresolved"
    _assert_empty_graph_context(ev)


def test_worktree_mismatch_is_stale(tmp_path) -> None:
    _seed_repo(tmp_path)
    status = {
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "index": {"reindexRecommended": False},
        "worktreeMismatch": {"worktreeRoot": "/repo/worktree", "indexRoot": "/repo/main"},
    }
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=status).fanin(action, str(tmp_path))
    assert ev.graph_freshness == "stale"
    assert ev.resolution_method == "unresolved"
    # mismatch remediation is a worktree-local index (setup-graph --fix), NOT a sync
    assert ev.fallback_reason and "worktree mismatch" in ev.fallback_reason.lower()
    assert "setup-graph --fix" in ev.fallback_reason
    assert "sync" not in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


def test_status_index_path_selects_non_default_codegraph_dir(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph-win"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "method:vl", "method", "validate_login", "LoginManager::validate_login", "src/auth.py", 10, 20)
    _node(con, "func:caller", "function", "caller", "caller", "src/caller.py", 1, 3)
    _edge(con, "func:caller", "method:vl", "calls")
    con.commit()
    con.close()
    status = {
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "index": {"reindexRecommended": False},
        "indexPath": str(cg_dir),
    }
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=status).fanin(action, str(tmp_path))
    assert ev.resolution_method == "location"
    assert ev.node_ids_resolved == ("method:vl",)
    assert ev.symbol_caller_count == 1


def test_highest_file_fanin_percentile_zero_fanin_is_no_impact_signal(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "leaf", "function", "leaf", "leaf", "src/leaf.py", 1, 2)
    for i in range(10):
        _node(con, f"f{i}", "function", f"f{i}", f"f{i}", f"src/f{i}.py", 1, 2)
    con.commit()
    con.close()

    got = cga.CodeGraphAdapter(status_fn=lambda repo_root: FRESH).highest_file_fanin_percentile(
        str(tmp_path / "src" / "leaf.py"), str(tmp_path)
    )

    assert got is None


def test_highest_file_fanin_percentile_opens_uri_sensitive_index_path(tmp_path) -> None:
    cg_dir = tmp_path / "index#with-chars"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "target", "function", "target", "target", "src/hot.py", 1, 2)
    _node(con, "caller", "function", "caller", "caller", "src/caller.py", 1, 2)
    _edge(con, "caller", "target")
    con.commit()
    con.close()
    status = {**FRESH, "indexPath": str(cg_dir)}

    got = cga.CodeGraphAdapter(status_fn=lambda repo_root: status).highest_file_fanin_percentile(
        str(tmp_path / "src" / "hot.py"), str(tmp_path)
    )

    assert got is not None and got > 0.0


def test_missing_db_returns_unresolved(tmp_path) -> None:
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))  # no .codegraph dir
    assert ev.resolution_method == "unresolved"
    assert ev.graph_freshness == "unknown"
    assert ev.fallback_reason and "setup-graph" in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


def test_cli_missing_returns_unresolved_with_install_hint(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = cga.CodeGraphAdapter(status_fn=lambda r: None).fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.fallback_reason and "install" in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


_OUT_OF_RANGE = {"pendingChanges": {"added": 0, "modified": 0, "removed": 0},
                 "index": {"reindexRecommended": False}, "version": "2.0.0"}


def test_out_of_range_runtime_version_is_untrusted(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=_OUT_OF_RANGE).fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.fallback_reason and "outside the accepted range" in ev.fallback_reason
    assert "setup-graph --fix" in ev.fallback_reason
    _assert_empty_graph_context(ev)


def test_out_of_range_runtime_version_omits_percentiles(tmp_path) -> None:
    _seed_repo(tmp_path)
    out = _adapter(status=_OUT_OF_RANGE).percentiles_by_name(
        ["src/auth.py::LoginManager::validate_login"], str(tmp_path)
    )
    assert out == {}


def test_corrupt_db_returns_unresolved_not_raises(tmp_path) -> None:
    # a non-SQLite / half-written file at the DB path must fail soft, never crash the assessment
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    (cg_dir / "codegraph.db").write_bytes(b"this is not a sqlite database")
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))  # must not raise
    assert ev.resolution_method == "unresolved"
    assert ev.symbol_fan_in_percentile == 0.0
    assert ev.fallback_reason and "codegraph DB" in ev.fallback_reason
    _assert_empty_graph_context(ev)


def test_empty_db_file_returns_unresolved_not_raises(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    (cg_dir / "codegraph.db").write_bytes(b"")  # zero-byte file -> no schema_versions table
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    _assert_empty_graph_context(ev)


def test_schema_below_v5_rejected(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db", schema_version=4)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.fallback_reason and "schema" in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


def test_name_fallback_single_match(tmp_path) -> None:
    _seed_repo(tmp_path)
    # no patch -> name fallback over affected_symbols
    action = CandidateAction(id="a1", label="p", action_type="edit",
                             affected_symbols=["src/auth.py::LoginManager::validate_login"])
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "name_fallback"
    assert ev.node_ids_resolved == ("method:vl",)
    assert ev.symbol_caller_count == 3


def test_name_fallback_ambiguous(tmp_path) -> None:
    _seed_repo(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    # a second symbol with the same qualified_name in the same file -> ambiguous
    _node(con, "method:vl2", "method", "validate_login", "LoginManager::validate_login",
          "src/auth.py", 100, 110)
    con.commit()
    con.close()
    action = CandidateAction(id="a1", label="p", action_type="edit",
                             affected_symbols=["src/auth.py::LoginManager::validate_login"])
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "name_fallback_ambiguous"
    assert set(ev.node_ids_resolved) == {"method:vl", "method:vl2"}
    # ambiguous agent-supplied names must NOT become trusted risk evidence: zero, untrusted fan-in
    assert ev.symbol_fan_in_percentile == 0.0
    assert ev.symbol_caller_count == 0
    assert ev.fallback_reason and "not trusted" in ev.fallback_reason.lower()
    _assert_empty_graph_context(ev)


def test_hunk_spanning_two_functions_resolves_both_and_unions_fanin(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "func:A", "function", "A", "A", "src/multi.py", 10, 15)
    _node(con, "func:B", "function", "B", "B", "src/multi.py", 20, 25)
    _node(con, "src:1", "function", "c1", "c1", "src/x.py", 1, 2)
    _node(con, "src:2", "function", "c2", "c2", "src/y.py", 1, 2)
    _edge(con, "src:1", "func:A", "calls")
    _edge(con, "src:2", "func:B", "calls")
    con.commit()
    con.close()
    patch = ("--- a/src/multi.py\n+++ b/src/multi.py\n"
             "@@ -12 +12 @@\n-x\n+y\n"
             "@@ -22 +22 @@\n-p\n+q\n")
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "location"
    assert set(ev.node_ids_resolved) == {"func:A", "func:B"}
    assert ev.symbol_caller_count == 2  # union of distinct callers across both changed functions


def test_gamma_style_multisymbol_graph_scope_routes_to_revise_safer(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    path = "src/Numerics/SpecialFunctions/Gamma.cs"
    _node(con, "method:GammaLn", "method", "GammaLn", "SpecialFunctions::GammaLn", path, 24, 80)
    _node(con, "method:Gamma", "method", "Gamma", "SpecialFunctions::Gamma", path, 88, 145)
    _node(con, "caller:pdf", "function", "Pdf", "GammaDistribution::Pdf", "src/Distribution/Gamma.cs", 1, 9)
    _node(con, "caller:cdf", "function", "Cdf", "GammaDistribution::Cdf", "src/Distribution/Gamma.cs", 10, 20)
    _edge(con, "caller:pdf", "method:Gamma", "calls")
    _edge(con, "caller:cdf", "method:GammaLn", "calls")
    con.commit()
    con.close()
    patch = (
        "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs b/src/Numerics/SpecialFunctions/Gamma.cs\n"
        "--- a/src/Numerics/SpecialFunctions/Gamma.cs\n"
        "+++ b/src/Numerics/SpecialFunctions/Gamma.cs\n"
        "@@ -45 +45 @@\n"
        "-                double s = GammaDk[0];\n"
        "+                double s = LanczosSum(z);\n"
        "@@ -110 +110 @@\n"
        "-                double s = GammaDk[0];\n"
        "+                double s = LanczosSum(z);\n"
    )
    action = CandidateAction(
        id="a1", label="gamma refactor", action_type="edit", proposed_patch=patch, expected_files=[path],
    )
    fanin = _adapter().fanin(action, str(tmp_path))
    inp = _worked_example_input()
    result = de.decide(ab.build_assessment(dataclasses.replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.45, "elicited_disutility": 0.80}],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=[],
            max_change_kind="UNKNOWN",
            consequential_symbol_changed=True,
            fallback_reason="no symbol diff supplied; C# file-level risk",
        ),
        fanin_evidence=fanin,
    )))

    assert fanin.resolution_method == "location"
    assert set(fanin.node_ids_resolved) == {"method:GammaLn", "method:Gamma"}
    assert fanin.resolved_symbol_count == 2
    assert result.recommended_decision is Decision.REVISE_SAFER


def test_context_bleed_does_not_grab_neighbor(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db")
    con = sqlite3.connect(str(cg_dir / "codegraph.db"))
    _node(con, "func:A", "function", "A", "A", "src/two.py", 10, 14)
    _node(con, "func:B", "function", "B", "B", "src/two.py", 15, 19)
    con.commit()
    con.close()
    # header old-range 13-16 bleeds into B via context, but only line 13 (in A) is removed
    patch = ("--- a/src/two.py\n+++ b/src/two.py\n"
             "@@ -13,4 +13,4 @@\n-line13\n line14\n line15\n line16\n+new\n")
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=patch)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.node_ids_resolved == ("func:A",)  # B not grabbed by trailing context


def test_unlocatable_symbol_is_unresolved_but_fresh(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit",
                             affected_symbols=["src/auth.py::does_not_exist"])
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.graph_freshness == "fresh"
    assert ev.symbol_fan_in_percentile == 0.0
    _assert_empty_graph_context(ev)


def test_windows_path_normalized_to_repo_relative(tmp_path) -> None:
    _seed_repo(tmp_path)
    # affected_symbols carrying an absolute Windows-style path under the repo root resolves
    abs_file = str(tmp_path / "src" / "auth.py")
    action = CandidateAction(id="a1", label="p", action_type="edit",
                             affected_symbols=[f"{abs_file}::LoginManager::validate_login"])
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "name_fallback"
    assert ev.node_ids_resolved == ("method:vl",)


# --- _default_status sequencing (STATUS-FIRST, conditional repair sync) over mocked subprocess ---


class _Recorder:
    """Fake subprocess.run: serves a queue of status payloads and records every argv. A status payload
    of None simulates a failed status probe (returncode 1)."""

    def __init__(self, status_payloads):
        self._status = list(status_payloads)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[1] == "status":
            payload = self._status.pop(0)
            if payload is None:
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout=_json.dumps(payload))
        return SimpleNamespace(returncode=0, stdout="")  # sync

    @property
    def sync_calls(self):
        return [c for c in self.calls if c[1] == "sync"]

    @property
    def status_calls(self):
        return [c for c in self.calls if c[1] == "status"]


def _patch(monkeypatch, recorder, *, on_path=True):
    # _default_status resolves the engine via find_engine() (not shutil.which directly), so mock that
    monkeypatch.setattr(cga, "find_engine", lambda: "/usr/bin/codegraph" if on_path else None)
    monkeypatch.setattr(cga.subprocess, "run", recorder)


def test_default_status_never_syncs_on_worktree_mismatch(monkeypatch) -> None:
    rec = _Recorder([{"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
                      "index": {"reindexRecommended": False},
                      "worktreeMismatch": {"worktreeRoot": "/wt", "indexRoot": "/main"}}])
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert rec.sync_calls == []  # the borrowed index must NOT be synced
    assert out and out.get("worktreeMismatch")


def test_default_status_never_syncs_when_uninitialized(monkeypatch) -> None:
    rec = _Recorder([{"initialized": False}])
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert rec.sync_calls == []
    assert out == {"initialized": False}


def test_default_status_no_sync_when_already_fresh(monkeypatch) -> None:
    rec = _Recorder([{"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
                      "index": {"reindexRecommended": False}}])
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert rec.sync_calls == [] and rec.status_calls and out is not None


def test_default_status_syncs_only_when_stale_initialized_same_worktree(monkeypatch) -> None:
    stale = {"initialized": True, "pendingChanges": {"added": 0, "modified": 1, "removed": 0},
             "index": {"reindexRecommended": False}}
    fresh = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
             "index": {"reindexRecommended": False}}
    rec = _Recorder([stale, fresh])  # initial=stale -> sync -> re-status=fresh
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert len(rec.sync_calls) == 1
    # A2/Windows: the SYNC invocation (the stale-repair path) must use the resolved full path, not the
    # bare "codegraph" name (which FileNotFoundErrors on the Windows .cmd shim).
    assert rec.sync_calls[0][0] == "/usr/bin/codegraph"
    assert len(rec.status_calls) == 2
    assert out == fresh  # returns the post-sync status


def test_default_status_returns_initial_when_post_sync_status_fails(monkeypatch) -> None:
    stale = {"initialized": True, "pendingChanges": {"added": 0, "modified": 1, "removed": 0},
             "index": {"reindexRecommended": False}}
    rec = _Recorder([stale, None])  # post-sync status probe fails -> fall back to the stale initial
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert len(rec.sync_calls) == 1
    assert rec.sync_calls[0][0] == "/usr/bin/codegraph"  # resolved full path, not bare name
    assert out == stale


def test_default_status_no_subprocess_when_binary_absent(monkeypatch) -> None:
    rec = _Recorder([])
    _patch(monkeypatch, rec, on_path=False)
    assert cga._default_status("/repo") is None
    assert rec.calls == []  # not even one spawn when codegraph is not on PATH


# --- percentiles_by_name (verify-path per-symbol fan-in lookup, A1) ---

def test_percentiles_by_name_resolves_known_symbol(tmp_path) -> None:
    _seed_repo(tmp_path)
    out = _adapter().percentiles_by_name(["src/auth.py::LoginManager::validate_login"], str(tmp_path))
    assert out == {"src/auth.py::LoginManager::validate_login": pytest.approx(1.0)}


def test_percentiles_by_name_resolves_class_method_dotted_id(tmp_path) -> None:
    # the VERIFY path passes AST-dotted qualified names ("LoginManager.validate_login"); codegraph
    # stores "::". _resolve_named must normalize so class methods resolve (Critical bug fix).
    _seed_repo(tmp_path)
    out = _adapter().percentiles_by_name(["src/auth.py::LoginManager.validate_login"], str(tmp_path))
    assert out == {"src/auth.py::LoginManager.validate_login": pytest.approx(1.0)}


def test_percentiles_by_name_omits_ambiguous(tmp_path) -> None:
    _seed_repo(tmp_path)
    con = sqlite3.connect(str(tmp_path / ".codegraph" / "codegraph.db"))
    _node(con, "method:vl2", "method", "validate_login", "LoginManager::validate_login",
          "src/auth.py", 100, 110)
    con.commit()
    con.close()
    out = _adapter().percentiles_by_name(["src/auth.py::LoginManager::validate_login"], str(tmp_path))
    assert out == {}  # ambiguous -> untrusted -> omitted (caller reads 0.0)


def test_percentiles_by_name_omits_unresolved(tmp_path) -> None:
    _seed_repo(tmp_path)
    out = _adapter().percentiles_by_name(["src/auth.py::does_not_exist"], str(tmp_path))
    assert out == {}


def test_percentiles_by_name_empty_when_stale(tmp_path) -> None:
    _seed_repo(tmp_path)
    out = _adapter(status=STALE).percentiles_by_name(
        ["src/auth.py::LoginManager::validate_login"], str(tmp_path)
    )
    assert out == {}


def test_percentiles_by_name_empty_when_db_absent(tmp_path) -> None:
    out = _adapter().percentiles_by_name(["src/auth.py::validate_login"], str(tmp_path))
    assert out == {}


def test_percentiles_by_name_empty_for_no_symbols(tmp_path) -> None:
    _seed_repo(tmp_path)
    assert _adapter().percentiles_by_name([], str(tmp_path)) == {}


def test_default_status_invokes_resolved_full_path_not_bare_name(monkeypatch) -> None:
    # A2/Windows: status/sync must run the resolved full path (shutil.which), never the bare name
    # ("codegraph"), or Windows FileNotFoundErrors on the .cmd shim even when installed.
    rec = _Recorder([{"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
                      "index": {"reindexRecommended": False}}])
    _patch(monkeypatch, rec)
    cga._default_status("/repo")
    assert rec.calls and all(c[0] == "/usr/bin/codegraph" for c in rec.calls)


# --- real binary path (skipped unless the codegraph CLI is installed) ---

@pytest.mark.requires_codegraph
def test_default_status_against_real_initialized_repo(tmp_path) -> None:
    import subprocess

    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    # resolve via find_engine (PATH or managed install), then build argv (cmd /c for the Windows .cmd
    # shim); utf-8 decode because codegraph emits UTF-8 progress output (cp1252 default raises on Windows)
    exe = cga.find_engine()
    subprocess.run(cga.resolve_engine_argv(exe, ["init", str(tmp_path)]),
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    status = cga._default_status(str(tmp_path))
    assert isinstance(status, dict)
    assert "pendingChanges" in status and "index" in status
    # a freshly indexed repo with no edits must read as fresh
    assert cga._is_fresh(status) is True
