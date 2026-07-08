"""Repo-relative path safety for adapters that READ caller-supplied file paths (RCA, bandit).

``expected_files`` come from the request/action and could (now, or via a future model/MCP surface)
contain absolute paths or ``..`` traversal. Any adapter that opens those paths must validate them
BEFORE reading/copying, so analysis can never touch files outside the repo. Invalid paths are dropped
(the caller degrades to projected / an evidence gap), never raised.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


def is_safe_relative(repo_root: str, rel: str) -> bool:
    """The single canonical repo-relative path-safety predicate.

    True iff ``rel`` is a safe repo-relative path under ``repo_root``: rejects the empty path, absolute
    paths, a drive or ``:``/NTFS-ADS component, any ``..`` component, and paths that resolve (e.g. via a
    symlink) outside ``repo_root``. Every adapter that reads/writes caller-supplied paths derives from
    this one predicate so an escape-class fix lands in exactly one place.
    """
    if not rel or PureWindowsPath(rel).drive or ":" in rel:
        return False
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        return False
    root = Path(repo_root).resolve()
    try:
        (root / p).resolve().relative_to(root)
    except ValueError:
        return False  # resolves outside the repo (e.g. drive-relative or a symlink escape)
    return True


def safe_relative_files(repo_root: str, files: list[str]) -> list[str]:
    """Return the subset of ``files`` that are safe repo-relative paths under ``repo_root``.

    The filtering form of ``is_safe_relative``. Order is preserved; the original path strings are
    returned; invalid paths are dropped (the caller degrades to projected / an evidence gap).
    """
    return [rel for rel in files if is_safe_relative(repo_root, rel)]
