"""Slice 5c — GraphProvider build-once memo.

One import-graph load/hash/(re)build per repo root per assessment, shared by the architecture map and
the blast walker, instead of each adapter calling get_import_graph independently. Adapter-only; no
graph payload crosses into core/. Adapters with no provider keep their direct-call behavior.
"""

from __future__ import annotations

from pebra.adapters import import_graph_cache as igc
from pebra.adapters.architecture_map import ArchitectureMapAdapter
from pebra.adapters.ast_import_graph import AstImportGraphAdapter
from pebra.core.models import CandidateAction


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _count_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = igc.get_import_graph

    def counted(root):
        calls.append(str(root))
        return real(root)

    monkeypatch.setattr(igc, "get_import_graph", counted)
    return calls


def test_graph_provider_memoizes_per_root(tmp_path, monkeypatch) -> None:
    _repo(tmp_path, {"a.py": "x = 1\n"})
    calls = _count_calls(monkeypatch)
    gp = igc.GraphProvider()
    gp.get(tmp_path)
    gp.get(tmp_path)
    assert len(calls) == 1  # second call served from the memo


def test_graph_provider_keys_by_root(tmp_path, monkeypatch) -> None:
    # distinct roots build separately (no cross-root collision); a repeated root is memoized.
    root_a = _repo(tmp_path / "a", {"x.py": "1\n"})
    root_b = _repo(tmp_path / "b", {"y.py": "1\n"})
    calls = _count_calls(monkeypatch)
    gp = igc.GraphProvider()
    gp.get(root_a)
    gp.get(root_b)
    gp.get(root_a)
    assert len(calls) == 2


def test_shared_provider_builds_once_across_adapters(tmp_path, monkeypatch) -> None:
    _repo(tmp_path, {"core.py": "x = 1\n", "a.py": "import core\n"})
    calls = _count_calls(monkeypatch)
    gp = igc.GraphProvider()
    ArchitectureMapAdapter(graph_provider=gp).gather_architecture(str(tmp_path), ["core.py"], "h")
    AstImportGraphAdapter(graph_provider=gp).blast(
        CandidateAction(id="a1", label="l", action_type="edit", expected_files=["core.py"]),
        str(tmp_path),
    )
    assert len(calls) == 1  # architecture build reused by blast


def test_adapters_without_provider_still_work(tmp_path) -> None:
    # backward compatible: no provider -> direct get_import_graph, behavior unchanged
    _repo(tmp_path, {
        "core.py": "x = 1\n", "a.py": "import core\n", "b.py": "import core\n", "c.py": "import core\n",
    })
    ev = ArchitectureMapAdapter().gather_architecture(str(tmp_path), ["core.py"], "h")
    assert "core.py" in ev.matched_anchors
    blast = AstImportGraphAdapter().blast(
        CandidateAction(id="a1", label="l", action_type="edit", expected_files=["core.py"]),
        str(tmp_path),
    )
    assert blast.direct_count == 3
