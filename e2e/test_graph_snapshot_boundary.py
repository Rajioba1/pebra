"""Physical import boundaries for provider-neutral repository exploration."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_exploration_core_and_port_do_not_reach_adapters_or_surfaces() -> None:
    files = (
        ROOT / "pebra" / "core" / "graph_snapshot.py",
        ROOT / "pebra" / "core" / "exploration.py",
        ROOT / "pebra" / "ports" / "repository_explorer_port.py",
    )
    forbidden = ("pebra.adapters", "pebra.app", "pebra.cli", "pebra.composition")

    violations = {
        path.name: sorted(module for module in _imports(path) if module.startswith(forbidden))
        for path in files
    }
    assert not {name: imports for name, imports in violations.items() if imports}


def test_cli_exploration_never_imports_assessment_or_scoring() -> None:
    imports = _imports(ROOT / "pebra" / "cli" / "explore.py")
    forbidden = {
        "pebra.app.assess_controller",
        "pebra.core.assessment_builder",
        "pebra.core.decision_engine",
    }

    assert imports.isdisjoint(forbidden)


def test_import_linter_declares_exploration_boundaries() -> None:
    config = (ROOT / ".importlinter").read_text(encoding="utf-8")

    assert "repository exploration core and port stay provider neutral" in config
    assert "CLI exploration never enters assessment scoring" in config
