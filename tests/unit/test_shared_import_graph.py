"""3b proof — the architecture map (AD-22) and the blast walker (AD-12) derive from ONE cached graph.

Before 3a each adapter scanned the repo independently and could disagree. Now both read the single
content-hash cache at ``.pebra/import_graph.json``: the architecture map's in-degree/anchors and the
blast walker's reverse-dependency counts are two views of the same edge set. These tests pin that.
"""

from __future__ import annotations

from pathlib import Path

from pebra.adapters import import_graph_cache as igc
from pebra.adapters.architecture_map import ArchitectureMapAdapter
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


def test_arch_in_degree_and_blast_dependents_agree_on_one_graph(tmp_path) -> None:
    # core.py is imported by three files. The arch map sees in-degree 3 (-> anchor); the blast walker
    # sees the same three as direct dependents. Same edge set, two views.
    root = _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    arch = ArchitectureMapAdapter().gather_architecture(root, ["core.py"], "head")
    blast = AstImportGraphAdapter().blast(_action(["core.py"]), root)
    assert "core.py" in arch.matched_anchors  # in-degree 3 -> anchor
    assert blast.direct_count == 3  # the very same three importers
    # exactly one cache file backs both adapters
    assert (tmp_path / ".pebra" / "import_graph.json").exists()


def test_both_adapters_read_the_same_persisted_edge_set(tmp_path) -> None:
    root = _repo(tmp_path, {"core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n"})
    # arch map builds + persists the cache
    ArchitectureMapAdapter().gather_architecture(root, ["core.py"], "head")
    cached = igc.load_import_graph(Path(root))
    assert cached is not None
    # blast reuses that persisted graph (warm) — no second scan, same in-degree the cache recorded
    AstImportGraphAdapter().blast(_action(["core.py"]), root)
    assert cached["in_degree"]["core.py"] == 2
