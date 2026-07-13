"""paths (Architecture §3) — repo root walk-up + source-neutral ``.pebra/`` init.

Adapter: pure-ish filesystem helpers (pathlib/os). Markers that anchor a repo root: ``.git`` or an
existing ``.pebra``. Falls back to the start directory if no marker is found.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT_MARKERS = (".git", ".pebra")


def find_repo_root(start_path: str) -> Path:
    start = Path(start_path).resolve()
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        for marker in _ROOT_MARKERS:
            if (candidate / marker).exists():
                return candidate
    return start


def ensure_pebra_dir(repo_root: Path) -> Path:
    pebra_dir = repo_root / ".pebra"
    pebra_dir.mkdir(parents=True, exist_ok=True)
    _ensure_git_excluded(repo_root)
    return pebra_dir


def _ensure_git_excluded(repo_root: Path) -> None:
    """Ignore runtime state locally without modifying the repository's tracked files."""
    if not (repo_root / ".git").exists():
        return
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if proc.returncode == 0 and proc.stdout.strip():
        exclude = Path(proc.stdout.strip())
        if not exclude.is_absolute():
            exclude = repo_root / exclude
    elif (repo_root / ".git").is_dir():
        exclude = repo_root / ".git" / "info" / "exclude"
    else:
        return
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        entry = ".pebra/"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if entry in {line.strip() for line in existing.splitlines()}:
            return
        separator = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + separator + entry + "\n", encoding="utf-8")
    except OSError:
        # Local exclusion is source-neutral hygiene, not an assessment prerequisite.
        return
