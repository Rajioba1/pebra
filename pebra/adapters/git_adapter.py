"""git_adapter (Architecture §3) — thin subprocess wrapper over git.

Adapter: subprocess is banned in core, allowed here. Returns plain values; never imports core/. All
calls degrade gracefully (return None / empty) when git is absent or the command fails, so PEBRA
never crashes on a non-git tree.
"""

from __future__ import annotations

import subprocess


def _run_git(repo_root: str, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",  # not the locale default (cp1252 on Windows mangles UTF-8 source)
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def head_commit(repo_root: str) -> str | None:
    out = _run_git(repo_root, ["rev-parse", "HEAD"])
    return out.strip() if out else None


def worktree_dirty(repo_root: str) -> bool | None:
    """True when tracked/staged/untracked files differ from HEAD; None when git is unavailable."""
    out = _run_git(repo_root, ["status", "--porcelain"])
    if out is None:
        return None
    return bool(out.strip())


def file_at_rev(repo_root: str, rev: str, path: str) -> str | None:
    """Return file contents at a git rev (e.g. 'HEAD'), or None if absent at that rev."""
    return _run_git(repo_root, ["show", f"{rev}:{path}"])


def changed_files(repo_root: str, scope: str) -> list[str]:
    """Changed file paths (POSIX-normalized) for the given scope.

    scope: ``staged`` (index vs HEAD), ``all`` (working tree vs HEAD + untracked), ``branch``
    (working tree vs HEAD — branch-base comparison is refined in a later phase).
    """
    if scope == "staged":
        out = _run_git(repo_root, ["diff", "--cached", "--name-only"])
        files = _lines(out)
    else:  # all | branch
        out = _run_git(repo_root, ["diff", "--name-only", "HEAD"])
        files = _lines(out)
        if scope == "all":
            untracked = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard"])
            files += _lines(untracked)
    # de-duplicate, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _lines(out: str | None) -> list[str]:
    if not out:
        return []
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]
