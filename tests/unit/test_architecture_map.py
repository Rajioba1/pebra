"""Architecture §5 / AD-22 — ArchitectureMapAdapter over the unified content-hash import graph.

Freshness is git-agnostic now: FRESH (graph up to date), REBUILT (content changed, re-parsed OK),
STALE (the rebuild itself failed), UNKNOWN (empty/missing repo). HEAD is provenance only.
"""

from __future__ import annotations

from pebra.adapters import import_graph_cache as igc
from pebra.adapters.architecture_map import ArchitectureMapAdapter
from pebra.core.constants import GraphFreshness
from pebra.ports.config_port import CriticalityGlob


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(tmp_path)


# --- nothing-to-map cases ---

def test_missing_repo_root_is_unknown_not_crash(tmp_path) -> None:
    ev = ArchitectureMapAdapter().gather_architecture(str(tmp_path / "nope"), [], "abc")
    assert ev.graph_freshness is GraphFreshness.UNKNOWN


def test_empty_repo_is_unknown(tmp_path) -> None:
    ev = ArchitectureMapAdapter().gather_architecture(str(tmp_path), [], "abc")
    assert ev.graph_freshness is GraphFreshness.UNKNOWN


# --- content-hash freshness ---

def test_first_build_is_fresh_and_writes_unified_cache(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.graph_freshness is GraphFreshness.FRESH
    assert ev.graph_commit == "abc"  # provenance only
    assert (tmp_path / ".pebra" / "import_graph.json").exists()


def test_unchanged_content_is_fresh(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    adapter = ArchitectureMapAdapter()
    adapter.gather_architecture(root, ["a.py"], "abc")
    ev = adapter.gather_architecture(root, ["a.py"], "abc")
    assert ev.graph_freshness is GraphFreshness.FRESH


def test_same_content_new_commit_is_still_fresh(tmp_path) -> None:
    # commit no longer drives freshness — same bytes under a new HEAD stays FRESH
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    adapter = ArchitectureMapAdapter()
    adapter.gather_architecture(root, ["a.py"], "abc")
    ev = adapter.gather_architecture(root, ["a.py"], "def")
    assert ev.graph_freshness is GraphFreshness.FRESH


def test_content_change_triggers_rebuilt(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    adapter = ArchitectureMapAdapter()
    adapter.gather_architecture(root, ["a.py"], "abc")
    (tmp_path / "a.py").write_text("import b\ny = 2\n", encoding="utf-8")  # bytes changed
    ev = adapter.gather_architecture(root, ["a.py"], "abc")
    assert ev.graph_freshness is GraphFreshness.REBUILT


def test_no_head_still_builds_fresh_not_unknown(tmp_path) -> None:
    # content-hash freshness is git-agnostic: a repo with files but no HEAD is FRESH
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], None)
    assert ev.graph_freshness is GraphFreshness.FRESH


def test_build_failure_is_stale(tmp_path, monkeypatch) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})

    def _boom(*a, **k):
        raise OSError("scan failed")

    monkeypatch.setattr(igc, "python_files", _boom)
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.graph_freshness is GraphFreshness.STALE


def test_recursion_error_during_build_is_stale(tmp_path, monkeypatch) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})

    def _boom(*a, **k):
        raise RecursionError("ast too deep")

    monkeypatch.setattr(igc, "python_files", _boom)
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.graph_freshness is GraphFreshness.STALE


# --- structural signals (over the cached graph) ---

def test_high_in_degree_file_is_god_node_anchor(tmp_path) -> None:
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["core.py"], "abc")
    assert ev.god_node_score > 0.0
    assert "core.py" in ev.matched_anchors
    assert ev.architecture_anchor_score > 0.0


def test_low_degree_file_matches_no_anchor(tmp_path) -> None:
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.matched_anchors == []


def test_relative_imports_count_toward_in_degree(tmp_path) -> None:
    root = _repo(tmp_path, {
        "pkg/core.py": "x = 1\n",
        "pkg/a.py": "from . import core\n",
        "pkg/b.py": "from .core import x\n",
        "pkg/c.py": "from . import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["pkg/core.py"], "abc")
    assert "pkg/core.py" in ev.matched_anchors
    assert ev.god_node_score > 0.0


def test_absolute_from_import_package_submodule_counts(tmp_path) -> None:
    root = _repo(tmp_path, {
        "pkg/__init__.py": "", "pkg/core.py": "x = 1\n",
        "a.py": "from pkg import core\n", "b.py": "from pkg import core\n", "c.py": "from pkg import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["pkg/core.py"], "abc")
    assert "pkg/core.py" in ev.matched_anchors
    assert ev.god_node_score > 0.0


def test_corrupt_cache_is_rebuilt_not_trusted(tmp_path) -> None:
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    adapter = ArchitectureMapAdapter()
    adapter.gather_architecture(root, ["a.py"], "abc")  # writes a valid cache
    cache = tmp_path / ".pebra" / "import_graph.json"
    cache.write_text("{}", encoding="utf-8")  # valid JSON, missing payload
    ev = adapter.gather_architecture(root, ["core.py"], "abc")
    assert "core.py" in ev.matched_anchors  # rebuilt from scratch, real anchors
    assert ev.god_node_score > 0.0


def test_god_node_score_is_fan_in_percentile(tmp_path) -> None:
    # 3f: god_node_score is the repo-relative fan-in percentile, not in_degree/max.
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["core.py"], "abc")
    assert ev.god_node_score == 1.0  # top fan-in in the repo


def test_tiny_repo_file_is_not_god_node(tmp_path) -> None:
    root = _repo(tmp_path, {"core.py": "x = 1\n", "a.py": "import core\n", "b.py": "y = 1\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["core.py"], "abc")
    assert ev.god_node_score == 0.0  # in-degree 1 is below the floor
    assert ev.matched_anchors == []


def test_fan_out_is_reported(tmp_path) -> None:
    root = _repo(tmp_path, {"hub.py": "import a\nimport b\n", "a.py": "x = 1\n", "b.py": "y = 1\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["hub.py"], "abc")
    assert ev.fan_out == 2


def test_cycle_participation_is_reported(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.cycle_participation is True


def test_no_cycle_participation_for_acyclic(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["a.py"], "abc")
    assert ev.cycle_participation is False


def test_decorated_file_is_domain_entrypoint(tmp_path) -> None:
    # 3e: a route handler counts as a domain entrypoint via decorator detection, not just filename.
    root = _repo(tmp_path, {
        "views.py": "@app.route('/x')\ndef h():\n    pass\n", "a.py": "import views\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(root, ["views.py"], "abc")
    assert ev.domain_entrypoint is True


def test_plain_file_is_not_domain_entrypoint(tmp_path) -> None:
    root = _repo(tmp_path, {"helper.py": "def f():\n    return 1\n", "a.py": "import helper\n"})
    ev = ArchitectureMapAdapter().gather_architecture(root, ["helper.py"], "abc")
    assert ev.domain_entrypoint is False


def test_domain_criticality_hint_from_config_glob(tmp_path) -> None:
    root = _repo(tmp_path, {"src/payments/charge.py": "x = 1\n", "src/util.py": "y = 1\n"})
    adapter = ArchitectureMapAdapter(criticality_globs=[CriticalityGlob("src/payments/**", "C4")])
    ev = adapter.gather_architecture(root, ["src/payments/charge.py"], "abc")
    assert ev.domain_criticality_hint == "C4"
