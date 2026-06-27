"""M5c.5 — codegraph_adapter: location-first per-symbol fan-in over a real (fixture) SQLite DB.

These tests build a tiny codegraph-shaped DB (schema subset, version 5) on disk and inject a fake
``status_fn`` so the full SQL + diff-parse + percentile math runs WITHOUT the codegraph binary. The
real ``codegraph status/sync`` subprocess path is covered separately behind ``requires_codegraph``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pebra.adapters import codegraph_adapter as cga
from pebra.core.models import CandidateAction

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
            end_column INTEGER, signature TEXT, updated_at INTEGER);
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


def test_reindex_recommended_is_stale(tmp_path) -> None:
    _seed_repo(tmp_path)
    status = {"pendingChanges": {"added": 0, "modified": 0, "removed": 0},
              "index": {"reindexRecommended": True}}
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter(status=status).fanin(action, str(tmp_path))
    assert ev.graph_freshness == "stale" and ev.resolution_method == "unresolved"


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


def test_missing_db_returns_unresolved(tmp_path) -> None:
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))  # no .codegraph dir
    assert ev.resolution_method == "unresolved"
    assert ev.graph_freshness == "unknown"
    assert ev.fallback_reason and "setup-graph" in ev.fallback_reason.lower()


def test_cli_missing_returns_unresolved_with_install_hint(tmp_path) -> None:
    _seed_repo(tmp_path)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = cga.CodeGraphAdapter(status_fn=lambda r: None).fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.fallback_reason and "install" in ev.fallback_reason.lower()


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


def test_empty_db_file_returns_unresolved_not_raises(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    (cg_dir / "codegraph.db").write_bytes(b"")  # zero-byte file -> no schema_versions table
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"


def test_schema_below_v5_rejected(tmp_path) -> None:
    cg_dir = tmp_path / ".codegraph"
    cg_dir.mkdir()
    _make_db(cg_dir / "codegraph.db", schema_version=4)
    action = CandidateAction(id="a1", label="p", action_type="edit", proposed_patch=_PATCH)
    ev = _adapter().fanin(action, str(tmp_path))
    assert ev.resolution_method == "unresolved"
    assert ev.fallback_reason and "schema" in ev.fallback_reason.lower()


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

import json as _json
from types import SimpleNamespace


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
    monkeypatch.setattr(cga.shutil, "which", lambda name: "/usr/bin/codegraph" if on_path else None)
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
    assert len(rec.status_calls) == 2
    assert out == fresh  # returns the post-sync status


def test_default_status_returns_initial_when_post_sync_status_fails(monkeypatch) -> None:
    stale = {"initialized": True, "pendingChanges": {"added": 0, "modified": 1, "removed": 0},
             "index": {"reindexRecommended": False}}
    rec = _Recorder([stale, None])  # post-sync status probe fails -> fall back to the stale initial
    _patch(monkeypatch, rec)
    out = cga._default_status("/repo")
    assert len(rec.sync_calls) == 1
    assert out == stale


def test_default_status_no_subprocess_when_binary_absent(monkeypatch) -> None:
    rec = _Recorder([])
    _patch(monkeypatch, rec, on_path=False)
    assert cga._default_status("/repo") is None
    assert rec.calls == []  # not even one spawn when codegraph is not on PATH


# --- real binary path (skipped unless the codegraph CLI is installed) ---

@pytest.mark.requires_codegraph
def test_default_status_against_real_initialized_repo(tmp_path) -> None:
    import subprocess

    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["codegraph", "init", str(tmp_path)], capture_output=True, text=True, timeout=180)
    status = cga._default_status(str(tmp_path))
    assert isinstance(status, dict)
    assert "pendingChanges" in status and "index" in status
    # a freshly indexed repo with no edits must read as fresh
    assert cga._is_fresh(status) is True
