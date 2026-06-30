"""Create a real temp git repo from the committed risky-repo fixture sources.

A real repo with a commit is required: PEBRA resolves the repo via ``.git`` and hashes the HEAD SHA
into the assessment. Mirrors the git-init pattern in tests/integration/test_mcp_server_handlers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FIXTURE_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "risky_repo"


def create_risky_repo(dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted(FIXTURE_SRC.glob("*.py")):
        shutil.copy2(src, dest / src.name)
    _git(dest, "init", "-q")
    _git(dest, "config", "user.email", "e2e@pebra.test")
    _git(dest, "config", "user.name", "pebra-e2e")
    _git(dest, "add", ".")
    _git(dest, "commit", "-q", "-m", "initial risky repo")
    return dest


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)
