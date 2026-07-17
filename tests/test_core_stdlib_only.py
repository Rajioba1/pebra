"""Architecture §2 / §4.3 — the one rule, enforced mechanically by AST walk.

`pebra/core/` is the pure deterministic engine: it may import only the pure standard-library
subset and other `pebra.core` modules. It must never import ports, adapters, app, surfaces,
third-party packages, or I/O-oriented stdlib (sqlite3, subprocess, argparse, ...).

This is belt-and-suspenders alongside the import-linter contracts (.importlinter): import-linter
guards the package graph; this test guards every physical import statement in core/, including
ones import-linter's external-package analysis might miss.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent / "pebra" / "core"

# Impure stdlib + every third-party/heavy lib core must never touch (Architecture §12 + §4 add-on).
FORBIDDEN_ROOTS = {
    # impure stdlib
    "sqlite3",
    "subprocess",
    "argparse",
    "socket",
    "asyncio",
    "threading",
    "multiprocessing",
    "urllib",
    "http",
    "ctypes",
    # §12 third-party
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "datasets",
    "pydriller",
    "swebench",
    "fastapi",
    "starlette",
    "uvicorn",
    "jinja2",
    "yaml",
    "radon",
    "bandit",
    "cryptography",
    "mapie",
    "textual",
    # §4 defense-in-depth add-on
    "numpy",
    "sklearn",
    "scikit_learn",
}

# Other pebra layers core must never import.
FORBIDDEN_PEBRA_SUBPACKAGES = {
    "ports",
    "adapters",
    "app",
    "cli",
    "mcp_server",
    "dashboard",
}


def _core_files() -> list[Path]:
    return sorted(p for p in CORE_DIR.rglob("*.py"))


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (module_dotted_path, level) for every import statement.

    level 0 = absolute import; level >= 1 = relative (core-internal) import.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return found


def test_core_files_exist() -> None:
    assert CORE_DIR.is_dir(), f"core package missing at {CORE_DIR}"


def test_core_imports_are_pure() -> None:
    violations: list[str] = []
    for path in _core_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, level in _imports(tree):
            if level > 0:
                # relative import — resolves inside pebra.core, allowed
                continue
            root = module.split(".")[0]
            if root == "pebra":
                parts = module.split(".")
                if len(parts) >= 2 and parts[1] in FORBIDDEN_PEBRA_SUBPACKAGES:
                    violations.append(f"{path.name}: imports {module} (other layer)")
            elif root in FORBIDDEN_ROOTS:
                violations.append(f"{path.name}: imports forbidden module {module}")
    assert not violations, "core/ purity violated:\n" + "\n".join(violations)
