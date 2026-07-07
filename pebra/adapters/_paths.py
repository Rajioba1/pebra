"""Repo-relative path safety for adapters that READ caller-supplied file paths (radon, bandit).

``expected_files`` come from the request/action and could (now, or via a future model/MCP surface)
contain absolute paths or ``..`` traversal. Any adapter that opens those paths must validate them
BEFORE reading/copying, so analysis can never touch files outside the repo. Invalid paths are dropped
(the caller degrades to projected / an evidence gap), never raised.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


def safe_relative_files(repo_root: str, files: list[str]) -> list[str]:
    """Return the subset of ``files`` that are safe repo-relative paths under ``repo_root``.

    Rejects absolute paths, any path containing a ``..`` component, and paths that resolve (e.g. via a
    symlink) outside ``repo_root``. Order is preserved; the original path strings are returned.
    """
    root = Path(repo_root).resolve()
    safe: list[str] = []
    for rel in files:
        if PureWindowsPath(rel).drive or ":" in rel:
            continue
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            continue
        try:
            (root / p).resolve().relative_to(root)
        except ValueError:
            continue  # resolves outside the repo (e.g. drive-relative or a symlink escape)
        safe.append(rel)
    return safe
