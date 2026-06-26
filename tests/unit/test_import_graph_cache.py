"""Slice 3a — unified import graph cache. Per-file SHA256 incremental build, git-agnostic freshness.

Both the architecture map and the blast walker derive from one cached graph (no double scan). Freshness
is content-hash based (graphify cache.py technique, reimplemented stdlib): FRESH = all hashes match,
REBUILT = some files changed and re-parsed OK, STALE = the (re)build itself failed, UNKNOWN = no graph
exists (empty/missing repo). HEAD is NOT part of the freshness decision.
"""

from __future__ import annotations

from pathlib import Path

from pebra.adapters import import_graph_cache as igc
from pebra.adapters._ast_utils import EDGE_CONFIDENCE
from pebra.core.constants import GraphFreshness


def _repo(tmp_path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_first_build_is_fresh_and_parses_all(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    payload, fr = igc.build_import_graph(root, prev_cache=None)
    assert fr is GraphFreshness.FRESH
    assert payload["in_degree"].get("b.py") == 1
    assert "a.py" in payload["file_hashes"] and "b.py" in payload["file_hashes"]


def test_empty_repo_is_unknown(tmp_path) -> None:
    _, fr = igc.build_import_graph(tmp_path, prev_cache=None)
    assert fr is GraphFreshness.UNKNOWN


def test_no_git_first_build_is_fresh_not_unknown(tmp_path) -> None:
    # content-hash freshness is git-agnostic: a repo with files but no HEAD is FRESH, never UNKNOWN
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    _, fr = igc.build_import_graph(root, prev_cache=None)
    assert fr is GraphFreshness.FRESH


def test_unchanged_content_is_fresh(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    _, fr = igc.build_import_graph(root, prev_cache=p1)
    assert fr is GraphFreshness.FRESH


def test_modified_file_is_rebuilt_and_reflects_change(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n", "c.py": "y = 1\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    assert p1["in_degree"].get("b.py") == 1
    (root / "a.py").write_text("import c\n", encoding="utf-8")  # now imports c, not b
    p2, fr = igc.build_import_graph(root, prev_cache=p1)
    assert fr is GraphFreshness.REBUILT
    assert p2["in_degree"].get("b.py", 0) == 0
    assert p2["in_degree"].get("c.py") == 1


def test_scan_failure_is_stale(tmp_path, monkeypatch) -> None:
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)

    def _boom(*a, **k):
        raise OSError("scan failed")

    monkeypatch.setattr(igc, "python_files", _boom)
    _, fr = igc.build_import_graph(root, prev_cache=p1)
    assert fr is GraphFreshness.STALE


def test_save_load_roundtrip(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    igc.save_import_graph(root, p1)
    loaded = igc.load_import_graph(root)
    assert loaded is not None
    assert loaded["in_degree"] == p1["in_degree"]


def test_get_import_graph_writes_then_warm_reads_fresh(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    _, fr1 = igc.get_import_graph(root)
    assert fr1 is GraphFreshness.FRESH  # first build
    assert (root / ".pebra" / "import_graph.json").exists()
    _, fr2 = igc.get_import_graph(root)
    assert fr2 is GraphFreshness.FRESH  # warm cache, nothing changed


def test_derive_reverse_maps_importers_with_confidence() -> None:
    edges = [{"src": "a.py", "tgt": "b.py", "kind": "static"}]
    rev = igc.derive_reverse(edges)
    assert rev["b.py"] == [("a.py", EDGE_CONFIDENCE["static"])]


def test_deleting_a_target_file_removes_its_phantom_in_degree(tmp_path) -> None:
    # a.py imports b.py; delete b.py. The carried edge a->b must NOT leave phantom in-degree for b.
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    assert p1["in_degree"].get("b.py") == 1
    (root / "b.py").unlink()
    p2, fr = igc.build_import_graph(root, prev_cache=p1)
    assert fr is GraphFreshness.REBUILT
    assert "b.py" not in p2["in_degree"]  # no phantom for the deleted target
    assert "b.py" not in p2["anchors"]


def test_adding_a_target_is_picked_up_by_an_existing_importer(tmp_path) -> None:
    # a.py imports b before b exists (unresolved); add b.py -> the a->b edge must now resolve.
    root = _repo(tmp_path, {"a.py": "import b\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    assert p1["in_degree"].get("b.py", 0) == 0  # unresolved at first
    (root / "b.py").write_text("x = 1\n", encoding="utf-8")
    p2, fr = igc.build_import_graph(root, prev_cache=p1)
    assert fr is GraphFreshness.REBUILT
    assert p2["in_degree"].get("b.py") == 1  # existing importer's edge now resolves


def test_unparseable_file_is_skipped_gracefully_not_crash(tmp_path) -> None:
    # a null-byte (unparseable) Python file must not crash the build; it's skipped (no edges from it),
    # while a valid importer of it still resolves the edge TO it.
    root = _repo(tmp_path, {"bad.py": "x = 1\x00\n", "user.py": "import bad\n"})
    payload, fr = igc.build_import_graph(root, prev_cache=None)
    assert fr is GraphFreshness.FRESH  # one unparseable file doesn't fail the whole graph
    assert payload["in_degree"].get("bad.py") == 1  # user.py -> bad.py edge still resolves
    assert payload["parse_error_files"] == ["bad.py"]


def test_tiny_repo_single_importer_is_not_an_anchor(tmp_path) -> None:
    # 3f: under the old `in_degree >= 0.8*max` rule a single importer made core an anchor. The floor
    # (min in-degree 3) fixes this tiny-repo over-anchoring.
    root = _repo(tmp_path, {"core.py": "x = 1\n", "a.py": "import core\n", "b.py": "y = 1\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert payload["in_degree"].get("core.py") == 1
    assert payload["anchors"] == []


def test_anchor_requires_floor_in_degree(tmp_path) -> None:
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert "core.py" in payload["anchors"]  # in-degree 3 meets the floor and tops the percentile


def test_out_degree_counts_outgoing_imports(tmp_path) -> None:
    root = _repo(tmp_path, {"hub.py": "import a\nimport b\n", "a.py": "x = 1\n", "b.py": "y = 1\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert payload["out_degree"]["hub.py"] == 2


def test_cycle_files_detects_import_cycle(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert set(payload["cycle_files"]) == {"a.py", "b.py"}


def test_no_cycle_files_for_acyclic_chain(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import c\n", "c.py": "x = 1\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert payload["cycle_files"] == []


def test_entrypoints_include_decorated_files(tmp_path) -> None:
    # 3e: a framework route handler is an entrypoint even though its filename is ordinary.
    root = _repo(tmp_path, {
        "views.py": "@app.route('/x')\ndef h():\n    pass\n", "util.py": "x = 1\n",
    })
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert "views.py" in payload["entrypoints"]
    assert "util.py" not in payload["entrypoints"]


def test_entrypoints_include_filename_based(tmp_path) -> None:
    root = _repo(tmp_path, {"main.py": "x = 1\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert "main.py" in payload["entrypoints"]


def test_entrypoints_recomputed_when_decorator_added(tmp_path) -> None:
    root = _repo(tmp_path, {"v.py": "def h():\n    pass\n", "a.py": "import v\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    assert "v.py" not in p1["entrypoints"]
    (root / "v.py").write_text("@app.route('/x')\ndef h():\n    pass\n", encoding="utf-8")
    p2, _ = igc.build_import_graph(root, prev_cache=p1)
    assert "v.py" in p2["entrypoints"]  # incremental reparse picks up the new decorator


def test_entrypoints_dropped_when_decorator_removed(tmp_path) -> None:
    # symmetric to the add case: a file that STOPS being an entrypoint must drop from the carry.
    root = _repo(tmp_path, {"v.py": "@app.route('/x')\ndef h():\n    pass\n", "a.py": "import v\n"})
    p1, _ = igc.build_import_graph(root, prev_cache=None)
    assert "v.py" in p1["entrypoints"]
    (root / "v.py").write_text("def h():\n    pass\n", encoding="utf-8")  # decorator removed
    p2, _ = igc.build_import_graph(root, prev_cache=p1)
    assert "v.py" not in p2["entrypoints"]  # stale entrypoint must not survive the incremental carry


def test_corrupt_cache_loads_as_none(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    cache = root / ".pebra" / "import_graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{ not json", encoding="utf-8")
    assert igc.load_import_graph(root) is None


def test_cache_with_malformed_edge_entries_loads_as_none(tmp_path) -> None:
    # edges is a list but entries aren't edge dicts -> must reject. Otherwise the FRESH path carries
    # these straight into _assemble, where e["tgt"] raises TypeError ('int' object not subscriptable).
    # Use the CURRENT schema_version so this exercises edge-shape validation, not a version mismatch.
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    cache = root / ".pebra" / "import_graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        f'{{"schema_version": "{igc.SCHEMA_VERSION}", "file_hashes": {{}}, "edges": [1, 2]}}',
        encoding="utf-8",
    )
    assert igc.load_import_graph(root) is None


def test_cache_with_edge_missing_required_keys_loads_as_none(tmp_path) -> None:
    # an edge dict missing "tgt"/"kind" would also crash _assemble / derive_reverse downstream.
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    cache = root / ".pebra" / "import_graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        f'{{"schema_version": "{igc.SCHEMA_VERSION}", "file_hashes": {{}}, "edges": [{{"src": "a.py"}}]}}',
        encoding="utf-8",
    )
    assert igc.load_import_graph(root) is None


def test_cache_with_non_dict_file_hashes_loads_as_none(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "x = 1\n"})
    cache = root / ".pebra" / "import_graph.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        f'{{"schema_version": "{igc.SCHEMA_VERSION}", "file_hashes": [], "edges": []}}',
        encoding="utf-8",
    )
    assert igc.load_import_graph(root) is None


def test_unresolved_edges_are_not_collapsed(tmp_path) -> None:
    # 3c-1: the per-target dedup previously collapsed EVERY tgt=None edge into one, destroying the
    # counts 3c needs. Now each unresolved/dynamic edge survives individually.
    root = _repo(tmp_path, {
        "m.py": (
            "import os\nimport sys\nimport importlib\n"
            "importlib.import_module('a')\nimportlib.import_module('b')\n"
        ),
    })
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    none_edges = [e for e in payload["edges"] if e["src"] == "m.py" and e["tgt"] is None]
    external = [e for e in none_edges if e["kind"] == "external"]
    dynamic = [e for e in none_edges if e["kind"] == "dynamic"]
    assert len(external) == 3  # os, sys, importlib — not collapsed to 1
    assert len(dynamic) == 2  # two importlib.import_module calls survive separately


def test_internal_vs_external_unresolved_distinguished_in_cache(tmp_path) -> None:
    # pkg/ exists -> `import pkg.missing` is an internal failure (static, None); `import requests`
    # is external (benign). Both survive as distinct edges with distinct kinds.
    root = _repo(tmp_path, {
        "pkg/__init__.py": "",
        "m.py": "import pkg.missing\nimport requests\n",
    })
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    none_edges = [e for e in payload["edges"] if e["src"] == "m.py" and e["tgt"] is None]
    assert sum(1 for e in none_edges if e["kind"] == "static") == 1  # pkg.missing
    assert sum(1 for e in none_edges if e["kind"] == "external") == 1  # requests


def test_unresolved_edges_carry_import_name(tmp_path) -> None:
    # 3d: the cache stores the import target string on unresolved edges so guidance can name it.
    root = _repo(tmp_path, {"b.py": "import billing.legacy\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    ext = [e for e in payload["edges"] if e["src"] == "b.py" and e["kind"] == "external"]
    assert ext and ext[0].get("name") == "billing.legacy"


def test_unresolved_edges_do_not_inflate_in_degree_or_total(tmp_path) -> None:
    # honesty guard: tgt=None edges must contribute nothing to in_degree / total_edges.
    root = _repo(tmp_path, {"m.py": "import os\nimport b\n", "b.py": "x = 1\n"})
    payload, _ = igc.build_import_graph(root, prev_cache=None)
    assert payload["in_degree"].get("b.py") == 1
    assert payload["total_edges"] == 1  # only the resolved m->b edge counts
