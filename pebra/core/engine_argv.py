"""Resolve CodeGraph launchers without executing Windows command shims."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


_GUIDANCE = "unsupported CodeGraph launcher; run: pebra setup-graph --fix"


class UnsafeEngineLauncherError(OSError):
    """A Windows command shim did not match a trusted direct-Node layout."""

    def __init__(self) -> None:
        super().__init__(_GUIDANCE)


def _node_file(path: Path) -> str | None:
    if not path.is_file() or (os.name == "nt" and path.name.lower() != "node.exe"):
        return None
    return str(path)


def _windows_codegraph_argv(launcher: Path, args: list[str]) -> list[str]:
    if launcher.name.lower() != "codegraph.cmd" or not launcher.is_file():
        raise UnsafeEngineLauncherError()

    root = launcher.parent.parent
    managed_node = root / "node.exe"
    managed_script = root / "lib" / "dist" / "bin" / "codegraph.js"
    if launcher.parent.name.lower() == "bin" and managed_node.is_file() and managed_script.is_file():
        return [str(managed_node), "--liftoff-only", str(managed_script), *args]

    npm_script = (
        launcher.parent / "node_modules" / "@colbymchenry" / "codegraph"
        / "dist" / "bin" / "codegraph.js"
    )
    sibling_node = launcher.parent / "node.exe"
    resolved_node = _node_file(sibling_node)
    if resolved_node is None:
        found_node = shutil.which("node")
        resolved_node = _node_file(Path(found_node)) if found_node else None
    if npm_script.is_file() and resolved_node is not None:
        return [resolved_node, str(npm_script), *args]
    raise UnsafeEngineLauncherError()


def resolve_engine_argv(exe: str, args: list[str]) -> list[str]:
    """Return a shell-free argv, rejecting unknown Windows ``.cmd``/``.bat`` launchers."""
    is_bare = not (os.path.isabs(exe) or "/" in exe or "\\" in exe)
    resolved = shutil.which(exe) if is_bare else exe
    if resolved is None:
        if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            raise UnsafeEngineLauncherError()
        return [exe, *args]
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return _windows_codegraph_argv(Path(resolved), args)
    return [resolved, *args]
