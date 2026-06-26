"""Shared AST import resolver for the architecture map (AD-22) and the blast-radius walker (AD-12).

Keeping one resolver means both graphs count edges identically — including relative imports, which a
naive resolver drops (systematically under-counting in-degree for package-relative codebases). Each
edge is classified by kind so the blast walker can attach edge-confidence weights.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

# Directories never scanned for the import graph (vcs, envs, caches, build artifacts).
SKIP_DIRS = {
    ".pebra", ".venv", ".git", "__pycache__", ".nox", "node_modules", "dist", "build",
}

# Entrypoint filenames (single shared definition so both walkers agree). __init__.py counts: it runs
# at import time and re-exports, so changing it is entry-point-like.
_ENTRYPOINT_NAMES = {"__init__.py", "__main__.py", "main.py"}


def is_entrypoint(posix_path: str) -> bool:
    base = posix_path.rsplit("/", 1)[-1]
    return base in _ENTRYPOINT_NAMES or base.startswith("handle")

# Edge-confidence weights (Architecture §5 clean-room): how much to trust each import kind as a
# real dependency edge. Used by the blast walker; not calibrated probabilities.
EDGE_CONFIDENCE = {"static": 0.85, "relative": 0.75, "wildcard": 0.35, "dynamic": 0.15, "unknown": 0.10}


def python_files(root: Path) -> list[str]:
    """Sorted posix rel-paths of repo Python files, excluding SKIP_DIRS. Shared by both walkers.

    Uses os.walk with ``followlinks=False`` and prunes skipped dirs in-place — this avoids following
    symlink loops (which rglob can) and skips entire excluded trees instead of filtering per file.

    ``.pyi`` stubs are included so stub-only modules still resolve — but a stub is dropped when its
    real ``.py`` sibling is present in the same directory (the module shadows its stub), so a module
    is represented exactly once and its imports aren't double-counted.
    """
    out: list[str] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        names = set(filenames)
        for name in filenames:
            if name.endswith(".py") or (name.endswith(".pyi") and name[:-1] not in names):
                out.append(Path(dirpath, name).relative_to(root).as_posix())
    return sorted(out)


@dataclass(frozen=True)
class ImportEdge:
    kind: str  # static | relative | wildcard | dynamic | external
    target: str | None  # resolved repo file (posix rel path) or None if outside the repo / unknown
    name: str | None = None  # the import target as written (e.g. "billing.legacy", ".sub.x"); for
    # dynamic imports the string-literal argument, or None when it isn't a literal. Used by 3d model
    # guidance to name WHAT couldn't be resolved; not part of edge identity.


def _top_level_names(fileset: set[str]) -> set[str]:
    """Top-level importable names present in the repo: a package directory's name, or a root-level
    module's stem. Used to tell an unresolved absolute import that points INTO the repo (a real
    resolution failure, kind 'static') from one pointing at a stdlib/third-party package (kind
    'external' — expected to be unresolved and not a sign of an incomplete graph)."""
    names: set[str] = set()
    for f in fileset:
        head, sep, _ = f.partition("/")
        names.add(head if sep else head.rsplit(".", 1)[0])  # dir name, or root module stem
    return names


def _abs_unresolved_kind(parts: list[str], top_level_names: set[str]) -> str:
    """Classify an absolute import that did NOT resolve: 'static' (internal failure) if its top-level
    name belongs to the repo, else 'external' (a library we never expected to resolve)."""
    return "static" if parts and parts[0] in top_level_names else "external"


def _resolve(parts: list[str], fileset: set[str]) -> str | None:
    # Resolution order: real module, then package, then their .pyi stubs. The .py forms are tried
    # first so a real module always wins over a stub of the same name. PEP-420 namespace packages
    # (a bare package dir with no __init__) have no single file target and stay unresolved — the
    # conservative false-negative direction; their submodules still resolve at the leaf.
    base = "/".join(parts)
    for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi", f"{base}/__init__.pyi"):
        if candidate in fileset:
            return candidate
    return None


def _relative_anchor(file_relpath: str, level: int) -> list[str] | None:
    """Package parts the relative import is anchored at (level 1 = current package).

    Returns ``None`` when the import reaches above the repo root (more leading dots than there are
    enclosing packages) — that's unresolvable, distinct from the empty-anchor top-level case where a
    level-1 relative import legitimately anchors at the repo root.
    """
    pkg = file_relpath.split("/")[:-1]  # drop the filename
    drop = level - 1
    if drop > len(pkg):
        return None
    return pkg[: len(pkg) - drop]


def _is_dynamic_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        return True
    return isinstance(func, ast.Name) and func.id == "__import__"


def _dynamic_name(node: ast.Call) -> str | None:
    """The string-literal module passed to importlib.import_module(...) / __import__(...), if any."""
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def _from_name(level: int, module: str | None, leaf: str) -> str:
    """Readable source spec for a `from ... import leaf` (e.g. 'pkg.core', '.sub.x', 'legacy.*')."""
    return "." * level + ".".join(p for p in (module, leaf) if p)


# Specific decorator leaves that mark a framework/test entrypoint even when used bare (@command,
# @tool, @app.route). These names are distinctive enough that a bare match is safe.
_ENTRYPOINT_DECORATOR_LEAVES = frozenset({
    "route", "command", "group", "callback", "tool", "resource", "prompt", "fixture",
})
# HTTP-method leaves are common method names, so they only count when QUALIFIED (@router.get, not a
# bare @get) — this avoids false-positive entrypoints in non-web code that happens to use such names.
_HTTP_VERB_LEAVES = frozenset({
    "get", "post", "put", "delete", "patch", "head", "options", "websocket",
})


def _decorator_path(dec: ast.expr) -> list[str]:
    """Dotted parts of a decorator expression: @app.route('/x') -> ['app', 'route']."""
    node: ast.AST = dec.func if isinstance(dec, ast.Call) else dec
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    return parts


def _is_entrypoint_decorator(dec: ast.expr) -> bool:
    path = _decorator_path(dec)
    if not path:
        return False
    leaf = path[-1]
    if leaf in _ENTRYPOINT_DECORATOR_LEAVES:
        return True
    if leaf in _HTTP_VERB_LEAVES and len(path) >= 2:  # @router.get yes, bare @get no
        return True
    return "pytest" in path


def _tree_is_entrypoint(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):  # pytest-style test entrypoint
                return True
            if any(_is_entrypoint_decorator(d) for d in node.decorator_list):
                return True
        elif isinstance(node, ast.ClassDef):
            if any(_is_entrypoint_decorator(d) for d in node.decorator_list):
                return True
    return False


def source_is_entrypoint(source: str) -> bool:
    """Does this source define a framework/test entrypoint (decorator- or test-name-based)?
    Decorator/name detection only — filename-based entrypoints are handled by ``is_entrypoint``."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return _tree_is_entrypoint(tree)


def parse_facts(
    file_relpath: str, source: str, fileset: set[str], top_level_names: set[str]
) -> tuple[list[ImportEdge], bool, bool]:
    """One parse per file -> (import edges, is_entrypoint, parse_error).

    Entrypoint = filename-based OR a framework/test decorator. Used by the cache build so each file
    is parsed exactly once. ``parse_error`` lets the blast evidence distinguish "clean zero edges"
    from "the changed file could not be inspected."
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], is_entrypoint(file_relpath), True  # fall back to the filename signal
    edges = _edges_from_tree(tree, file_relpath, fileset, top_level_names)
    return edges, is_entrypoint(file_relpath) or _tree_is_entrypoint(tree), False


def iter_import_edges(
    file_relpath: str, source: str, fileset: set[str], top_level_names: set[str] | None = None
) -> list[ImportEdge]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # unparseable file (syntax error, or null bytes -> ValueError on older Pythons): contribute
        # no edges, like any other file we can't read. One bad file never fails the whole graph build.
        return []

    # repo top-level names, for classifying unresolved absolute imports as internal vs external.
    # Computed once per build by the caller; derived here when called standalone (e.g. unit tests).
    tl = top_level_names if top_level_names is not None else _top_level_names(fileset)
    return _edges_from_tree(tree, file_relpath, fileset, tl)


def _edges_from_tree(
    tree: ast.AST, file_relpath: str, fileset: set[str], tl: set[str]
) -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                target = _resolve(parts, fileset)
                kind = "static" if target is not None else _abs_unresolved_kind(parts, tl)
                edges.append(ImportEdge(kind, target, name=alias.name))
        elif isinstance(node, ast.ImportFrom):
            wildcard = any(a.name == "*" for a in node.names)
            kind = "wildcard" if wildcard else ("relative" if node.level > 0 else "static")
            if node.level > 0:
                anchor = _relative_anchor(file_relpath, node.level)
                if anchor is None:
                    # the relative import escapes the repo root: unresolvable, but still a real
                    # (relative) edge attempt — record it with no target rather than mis-resolving.
                    leaf = "*" if wildcard else (node.names[0].name if node.names else "")
                    edges.append(ImportEdge(kind, None, name=_from_name(node.level, node.module, leaf)))
                    continue
            else:
                anchor = []
            base = anchor + (node.module.split(".") if node.module else [])
            if wildcard:
                name = _from_name(node.level, node.module, "*")
                edges.append(ImportEdge(kind, _resolve(base, fileset) if base else None, name=name))
            else:
                # each imported name may itself be a submodule (`from pkg import core` -> pkg/core.py);
                # try that first, then fall back to the module/package where the name is defined.
                for alias in node.names:
                    target = _resolve(base + [alias.name], fileset)
                    if target is None and base:
                        target = _resolve(base, fileset)
                    name = _from_name(node.level, node.module, alias.name)
                    if target is None and node.level == 0:
                        # unresolved absolute from-import: internal failure vs external library
                        edges.append(ImportEdge(_abs_unresolved_kind(base, tl), None, name=name))
                    else:
                        edges.append(ImportEdge(kind, target, name=name))
        elif _is_dynamic_call(node):
            edges.append(ImportEdge("dynamic", None, name=_dynamic_name(node)))
    return edges
