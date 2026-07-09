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
# `e2e/out/` is the gitignored runtime area: external repo clones (incl. their node_modules) and run
# artifacts, NOT e2e source. It must not be scanned — those are third-party trees that may not even be
# valid Python 3 (a tab-indented dep script would raise, and a clone can't smuggle an e2e `import pebra`).
_EXCLUDED_TOP = frozenset({"out"})


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
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and (node.args[0].value == "pebra" or node.args[0].value.startswith("pebra."))
            ):
                hits.append(node.args[0].value)
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and (node.args[0].value == "pebra" or node.args[0].value.startswith("pebra."))
            ):
                hits.append(node.args[0].value)
    return hits


def test_pebra_import_scanner_rejects_dynamic_in_process_imports(tmp_path):
    py = tmp_path / "bad.py"
    py.write_text(
        "import importlib\n"
        "importlib.import_module('pebra.dashboard.server')\n"
        "__import__('pebra.adapters.store.db')\n",
        encoding="utf-8",
    )
    assert _pebra_imports(py) == ["pebra.dashboard.server", "pebra.adapters.store.db"]


def test_pebra_import_scanner_allows_subprocess_code_strings(tmp_path):
    py = tmp_path / "ok.py"
    py.write_text(
        "CODE = 'from pebra.dashboard.server import serve\\n'\n",
        encoding="utf-8",
    )
    assert _pebra_imports(py) == []


def test_no_e2e_module_imports_pebra_internals():
    offenders: dict[str, list[str]] = {}
    for py in sorted(_E2E_ROOT.rglob("*.py")):
        rel = py.relative_to(_E2E_ROOT)
        if rel.parts and rel.parts[0] in _EXCLUDED_TOP:
            continue  # skip gitignored external clones / run artifacts under e2e/out/
        hits = _pebra_imports(py)
        if hits:
            offenders[str(py.relative_to(_E2E_ROOT))] = hits
    assert not offenders, (
        "e2e must reach PEBRA only via the CLI/MCP/HTTP boundary, never by importing it. "
        f"Offending imports: {offenders}"
    )
