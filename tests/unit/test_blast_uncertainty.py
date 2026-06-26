"""3c-2 — graph-incompleteness as evidence on BlastEvidence (AD-12 uncertainty axis).

The blast walker now reports HOW MUCH of the impact estimate it couldn't resolve: unresolved internal
imports, dynamic imports, wildcard imports, missing expected files, and dynamic/wildcard imports
elsewhere that could secretly depend on the changed file. These feed a bounded graph_uncertainty_score
(capped, never collapses confidence to zero) plus a human-facing reason. External/stdlib imports are
tracked but NEVER penalized — they are not a sign of an incomplete graph.

Honesty rule (kept here): unresolved edges NEVER inflate direct_count/transitive_count. Unknown impact
is reported as low confidence, not as fake dependents.
"""

from __future__ import annotations

import pytest

from pebra.adapters.ast_import_graph import AstImportGraphAdapter
from pebra.core.constants import GRAPH_UNCERTAINTY_CAP
from pebra.core.models import BlastEvidence, CandidateAction


def _action(files):
    return CandidateAction(id="a1", label="l", action_type="edit", expected_files=files)


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_blast_evidence_uncertainty_fields_default_to_clean() -> None:
    ev = BlastEvidence()
    assert ev.graph_uncertainty_score == 0.0
    assert ev.graph_uncertainty_reason == ""
    assert ev.missing_file_count == 0
    assert ev.unresolved_import_count == 0
    assert ev.dynamic_import_count == 0
    assert ev.wildcard_import_count == 0
    assert ev.external_import_count == 0


def test_fully_resolved_repo_has_zero_uncertainty(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "x = 1\n", "a.py": "import b\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.graph_uncertainty_score == 0.0
    assert ev.graph_uncertainty_reason == ""


def test_external_imports_are_tracked_but_not_penalized(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "import os\nimport sys\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.external_import_count == 2
    assert ev.graph_uncertainty_score == 0.0  # stdlib imports are not incompleteness


def test_internal_unresolved_import_raises_uncertainty(tmp_path) -> None:
    root = _repo(tmp_path, {"pkg/__init__.py": "", "b.py": "import pkg.missing\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.unresolved_import_count == 1
    assert ev.graph_uncertainty_score > 0.0
    assert "unresolved" in ev.graph_uncertainty_reason.lower()


def test_dynamic_import_raises_uncertainty(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "import importlib\nimportlib.import_module('x')\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.dynamic_import_count == 1
    assert ev.graph_uncertainty_score > 0.0
    assert "dynamic" in ev.graph_uncertainty_reason.lower()


def test_wildcard_import_raises_uncertainty(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "from c import *\n", "c.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.wildcard_import_count == 1
    assert ev.graph_uncertainty_score > 0.0


def test_missing_expected_file_raises_uncertainty(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py", "ghost.py"]), root)
    assert ev.missing_file_count == 1
    assert ev.graph_uncertainty_score > 0.0
    assert "missing" in ev.graph_uncertainty_reason.lower()


def test_dynamic_import_elsewhere_raises_uncertainty_for_clean_file(tmp_path) -> None:
    # the changed file is clean, but another file's dynamic import could secretly depend on it.
    root = _repo(tmp_path, {
        "b.py": "x = 1\n",
        "other.py": "import importlib\nimportlib.import_module('b')\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.dynamic_import_count == 0  # nothing dynamic in the changed file itself
    assert ev.graph_uncertainty_score > 0.0  # whole-graph hidden-dependent risk fires
    assert "hide dependents" in ev.graph_uncertainty_reason.lower()


def test_uncertainty_score_is_bounded_by_cap(tmp_path) -> None:
    body = "import importlib\n" + "".join(
        f"importlib.import_module('m{i}')\n" for i in range(200)
    )
    root = _repo(tmp_path, {"b.py": body})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.graph_uncertainty_score <= GRAPH_UNCERTAINTY_CAP


def test_unresolved_edges_never_inflate_blast_counts(tmp_path) -> None:
    # honesty guard: a file full of dynamic/external imports has NO real dependents -> zero blast,
    # but high uncertainty. "low blast" must not be confused with "confidently safe".
    root = _repo(tmp_path, {"b.py": "import os\nimport importlib\nimportlib.import_module('z')\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.direct_count == 0
    assert ev.transitive_count == 0
    assert ev.graph_uncertainty_score > 0.0


def test_request_supplied_evidence_has_no_uncertainty(tmp_path) -> None:
    ev = AstImportGraphAdapter({"direct_count": 5}).blast(_action(["x.py"]), str(tmp_path))
    assert ev.direct_count == 5
    assert ev.graph_uncertainty_score == 0.0  # real walk not attempted; defaults stand


def test_unresolved_import_names_surface_in_blast(tmp_path) -> None:
    root = _repo(tmp_path, {"pkg/__init__.py": "", "b.py": "import pkg.missing\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert any("pkg.missing" in s for s in ev.unresolved_imports)
    assert any(s.startswith("b.py") for s in ev.unresolved_imports)


def test_dynamic_import_names_surface_in_blast(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "import importlib\nimportlib.import_module('plug.x')\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert any("plug.x" in s for s in ev.dynamic_imports)


def test_missing_files_surface_in_blast(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "x = 1\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py", "ghost.py"]), root)
    assert "ghost.py" in ev.missing_files


def test_clean_repo_has_empty_provenance_lists(tmp_path) -> None:
    root = _repo(tmp_path, {"b.py": "x = 1\n", "a.py": "import b\n"})
    ev = AstImportGraphAdapter().blast(_action(["b.py"]), root)
    assert ev.unresolved_imports == ()
    assert ev.dynamic_imports == ()
    assert ev.wildcard_imports == ()
    assert ev.missing_files == ()


def test_ghost_edit_does_not_accrue_whole_graph_hidden_dependent_risk(tmp_path) -> None:
    # the edited file is absent from the repo -> nothing can depend on it, so whole-graph
    # dynamic/wildcard "hidden dependent" risk is moot. Only missing_file_count should signal
    # incompleteness; the repo_* terms must NOT fire at full graph scale for a ghost edit.
    root = _repo(tmp_path, {
        "other.py": "import importlib\nimportlib.import_module('z')\nfrom q import *\n",
        "q.py": "x = 1\n",
    })
    ev = AstImportGraphAdapter().blast(_action(["ghost.py"]), root)
    assert ev.missing_file_count == 1
    assert ev.graph_uncertainty_score == pytest.approx(0.05)  # missing-file penalty only


def test_unparseable_changed_file_raises_uncertainty(tmp_path) -> None:
    # A file that exists but cannot be parsed must not look like a clean zero-blast edit. It has
    # unknown impact, so the graph channel lowers confidence and names the parse-failed file.
    root = _repo(tmp_path, {"bad.py": "x = 1\x00\n"})
    ev = AstImportGraphAdapter().blast(_action(["bad.py"]), root)
    assert ev.direct_count == 0
    assert ev.transitive_count == 0
    assert ev.parse_error_count == 1
    assert ev.graph_uncertainty_score > 0.0
    assert "parse" in ev.graph_uncertainty_reason.lower()
    assert "bad.py" in ev.parse_error_files
