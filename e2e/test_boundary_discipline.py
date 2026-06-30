"""The agent-boundary enforcer: NO module under e2e/ may import pebra.

The whole point of an agent/product e2e is that PEBRA is exercised as an external process (CLI argv /
MCP stdio / HTTP), never by reaching into its Python internals. import-linter can't express this (its
root package is ``pebra``, so it only stops ``pebra -> e2e``), so we scan the e2e tree's ASTs directly.
A single ``import pebra`` / ``from pebra...`` anywhere under e2e/ fails this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

_E2E_ROOT = Path(__file__).parent


def _pebra_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name == "pebra" or a.name.startswith("pebra.")]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "pebra" or mod.startswith("pebra."):
                hits.append(mod)
    return hits


def test_no_e2e_module_imports_pebra_internals():
    offenders: dict[str, list[str]] = {}
    for py in sorted(_E2E_ROOT.rglob("*.py")):
        hits = _pebra_imports(py)
        if hits:
            offenders[str(py.relative_to(_E2E_ROOT))] = hits
    assert not offenders, (
        "e2e must reach PEBRA only via the CLI/MCP/HTTP boundary, never by importing it. "
        f"Offending imports: {offenders}"
    )
