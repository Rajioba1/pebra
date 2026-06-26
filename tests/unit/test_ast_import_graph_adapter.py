"""AD-12 — AstImportGraphAdapter real blast walk (BlastRadiusProvider).

Blast radius = who DEPENDS on the changed file (reverse import graph), with depth buckets and
per-edge confidence. Request-supplied blast (Phase-0 fixture path) still takes precedence.
"""

from __future__ import annotations

from pebra.adapters.ast_import_graph import AstImportGraphAdapter
from pebra.core.models import CandidateAction


def _action(files):
    return CandidateAction(id="a1", label="l", action_type="edit", expected_files=files)


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_request_supplied_blast_takes_precedence(tmp_path) -> None:
    adapter = AstImportGraphAdapter({"direct_count": 5, "transitive_count": 2})
    ev = adapter.blast(_action(["x.py"]), str(tmp_path))
    assert ev.direct_count == 5  # real walk not attempted


def test_direct_dependent_is_counted(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.direct_count == 1  # a.py imports b


def test_depth_buckets_for_dependency_chain(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import c\n", "c.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["c.py"]), root)
    assert ev.direct_count == 1  # b depends on c
    assert ev.transitive_count == 1  # a depends transitively
    assert ev.depth_buckets.get(1) == 1
    assert ev.depth_buckets.get(2) == 1


def test_no_dependents_is_zero_blast(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["a.py"]), root)  # nobody imports a
    assert ev.direct_count == 0
    assert ev.transitive_count == 0


def test_wildcard_edge_lowers_confidence(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "from b import *\n", "b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.edge_confidence_min <= 0.35
    assert ev.low_confidence_edge_count >= 1


def test_static_edges_are_high_confidence(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.edge_confidence_mean >= 0.85
    assert ev.low_confidence_edge_count == 0


def test_import_cycle_detected(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    ev = AstImportGraphAdapter().blast(_action(["a.py"]), root)
    assert ev.import_cycle_detected is True


def test_chain_has_no_cycle(tmp_path) -> None:
    root = _repo(tmp_path, {"a.py": "import b\n", "b.py": "import c\n", "c.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["c.py"]), root)
    assert ev.import_cycle_detected is False


def test_entrypoint_signal_when_dependent_is_main(tmp_path) -> None:
    root = _repo(tmp_path, {"main.py": "import b\n", "b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.entrypoint_signal is True


def test_entrypoint_signal_when_changed_file_is_decorated_route(tmp_path) -> None:
    # 3e: editing a framework route handler is entrypoint-like even with an ordinary filename.
    root = _repo(tmp_path, {"views.py": "@app.route('/x')\ndef h():\n    pass\n"})
    ev = AstImportGraphAdapter().blast(_action(["views.py"]), root)
    assert ev.entrypoint_signal is True


def test_entrypoint_signal_when_dependent_is_decorated(tmp_path) -> None:
    root = _repo(tmp_path, {
        "views.py": "import b\n@app.route('/x')\ndef h():\n    pass\n", "b.py": "x = 1\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.entrypoint_signal is True  # a decorated dependent reaches an entrypoint


def test_missing_repo_is_empty_blast(tmp_path) -> None:
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), str(tmp_path / "nope"))
    assert ev.direct_count == 0


def test_relative_dependents_are_counted(tmp_path) -> None:
    root = _repo(tmp_path, {"pkg/core.py": "x = 1\n", "pkg/a.py": "from . import core\n"})
    ev = AstImportGraphAdapter().blast(_action(["pkg/core.py"]), root)
    assert ev.direct_count == 1  # relative import must count


def test_diamond_counts_each_direct_dependent_once(tmp_path) -> None:
    # a and b both import t; the confidence list must match the dependent count (H-1 fix)
    root = _repo(tmp_path, {"t.py": "x = 1\n", "a.py": "import t\n", "b.py": "import t\n"})
    ev = AstImportGraphAdapter().blast(_action(["t.py"]), root)
    assert ev.direct_count == 2
    assert ev.low_confidence_edge_count <= ev.direct_count + ev.transitive_count


def test_depth_three_boundary_excludes_fourth_hop(tmp_path) -> None:
    root = _repo(tmp_path, {
        "t.py": "x = 1\n",
        "d1.py": "import t\n", "d2.py": "import d1\n", "d3.py": "import d2\n", "d4.py": "import d3\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["t.py"]), root)
    assert ev.depth_buckets == {1: 1, 2: 1, 3: 1}  # d1,d2,d3
    assert ev.transitive_count == 2  # d2,d3 ; d4 (depth 4) excluded


def test_multiple_changed_files_aggregate_dependents(tmp_path) -> None:
    root = _repo(tmp_path, {
        "b.py": "x = 1\n", "c.py": "y = 1\n", "db.py": "import b\n", "dc.py": "import c\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["b.py", "c.py"]), root)
    assert ev.direct_count == 2


def test_from_package_import_counts_submodule_dependent(tmp_path) -> None:
    # `from pkg import core` must register a.py as a dependent of pkg/core.py
    root = _repo(tmp_path, {
        "pkg/__init__.py": "", "pkg/core.py": "x = 1\n", "a.py": "from pkg import core\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["pkg/core.py"]), root)
    assert ev.direct_count == 1


def test_cycle_not_touching_changed_file_does_not_fire(tmp_path) -> None:
    # x<->y is a cycle, but the changed file t reaches neither -> import_cycle_detected stays False
    root = _repo(tmp_path, {"t.py": "z = 1\n", "x.py": "import y\n", "y.py": "import x\n"})
    ev = AstImportGraphAdapter().blast(_action(["t.py"]), root)
    assert ev.import_cycle_detected is False
