"""paths (Architecture §3) — repo root walk-up + ``.pebra/`` init + .gitignore hygiene.

Adapter: pure-ish filesystem helpers (pathlib/os). Markers that anchor a repo root: ``.git`` or an
existing ``.pebra``. Falls back to the start directory if no marker is found.
"""

from __future__ import annotations

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
    _ensure_gitignored(repo_root)
    return pebra_dir


def _ensure_gitignored(repo_root: Path) -> None:
    gitignore = repo_root / ".gitignore"
    entry = ".pebra/"
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if entry not in {line.strip() for line in lines}:
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write(f"\n{entry}\n")
    elif (repo_root / ".git").exists():
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
