"""Resolve trusted Node launchers without executing Windows command shims."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


_GUIDANCE = "unsupported CodeGraph launcher; run: pebra setup-graph --fix"


class UnsafeEngineLauncherError(OSError):
    """A Windows command shim did not match a trusted direct-Node layout."""

    def __init__(self) -> None:
        super().__init__(_GUIDANCE)


def _node_file(path: Path, *, windows: bool) -> str | None:
    if not path.is_file() or (windows and path.name.lower() != "node.exe"):
        return None
    return str(path)


def _resolved_node(launcher_dir: Path, *, windows: bool) -> str | None:
    resolved = _node_file(launcher_dir / "node.exe", windows=windows)
    if resolved is not None:
        return resolved
    found = shutil.which("node")
    return _node_file(Path(found), windows=windows) if found else None


def _windows_npm_argv(launcher: Path, args: list[str]) -> list[str]:
    if launcher.name.lower() != "npm.cmd" or not launcher.is_file():
        raise UnsafeEngineLauncherError()
    script = launcher.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    node = _resolved_node(launcher.parent, windows=True)
    if script.is_file() and node is not None:
        return [node, str(script), *args]
    raise UnsafeEngineLauncherError()


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
    resolved_node = _resolved_node(launcher.parent, windows=True)
    if npm_script.is_file() and resolved_node is not None:
        return [resolved_node, str(npm_script), *args]
    raise UnsafeEngineLauncherError()


def _resolve_engine_argv(exe: str, args: list[str], *, os_name: str) -> list[str]:
    """Resolve using an explicit OS policy while retaining the host-native filesystem ``Path``."""
    is_bare = not (os.path.isabs(exe) or "/" in exe or "\\" in exe)
    resolved = shutil.which(exe) if is_bare else exe
    if resolved is None:
        if os_name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            raise UnsafeEngineLauncherError()
        return [exe, *args]
    if os_name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        if Path(resolved).name.lower() == "npm.cmd":
            return _windows_npm_argv(Path(resolved), args)
        return _windows_codegraph_argv(Path(resolved), args)
    return [resolved, *args]


def resolve_engine_argv(exe: str, args: list[str]) -> list[str]:
    """Return a shell-free argv, rejecting unknown Windows ``.cmd``/``.bat`` launchers."""
    return _resolve_engine_argv(exe, args, os_name=os.name)
