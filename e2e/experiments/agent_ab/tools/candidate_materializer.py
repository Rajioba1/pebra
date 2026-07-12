"""candidate_materializer (P4, e2e-side) — apply the agent's narrowed candidate to a scratch copy.

The ``pebra_graph_repair`` arm verifies a resubmitted candidate by running its covering tests. That
needs the candidate MATERIALIZED (patch applied) somewhere the real repo isn't touched. This copies the
CURRENT working tree (so earlier accepted edits to OTHER files are included) into a throwaway dir, git-
inits it, and applies the patch VERBATIM. The exact same patch string is what ``candidate_verifier``
hashes, so the verified evidence binds this materialization to that patch.

Deliberately duplicates the "apply an exact patch to a scratch tree, fail-closed" recipe rather than
importing pebra's ``patch_materializer`` — e2e must NOT ``import pebra`` (enforced boundary). Fail
CLOSED: any non-clean apply returns None (never a partial/best-effort materialization). Never mutates
``repo_path``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

_IGNORE = shutil.ignore_patterns(
    ".git", ".codegraph", ".pebra", "bin", "obj", "node_modules", "__pycache__"
)


def _git(cwd: Path, *args: str, timeout: float = 60) -> bool:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=max(0.001, timeout),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _git_init(cwd: Path, remaining: Any) -> bool:
    return _git(cwd, "init", "-q", timeout=remaining()) and _git(
        cwd, "config", "core.autocrlf", "false", timeout=remaining()
    )


def materialize_candidate(
    repo_path: Path | str, patch_text: str, *, timeout_seconds: float = 60
) -> Path | None:
    """Copy repo_path's current working tree into a throwaway dir and apply ``patch_text`` verbatim.
    Returns the scratch dir, or None if the copy/init/apply fails (fail-closed). Caller must ``cleanup``."""
    src = Path(repo_path)
    deadline = time.monotonic() + max(0.001, timeout_seconds)

    def _remaining() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(["candidate-materializer"], timeout_seconds)
        return remaining

    scratch = Path(tempfile.mkdtemp(prefix="pebra-candidate-"))
    dest = scratch / "repo"
    try:
        shutil.copytree(src, dest, ignore=_IGNORE, symlinks=False)
        _remaining()
    except (OSError, shutil.Error, subprocess.TimeoutExpired):
        cleanup(scratch)
        return None
    try:
        initialized = _git_init(dest, _remaining)
    except subprocess.TimeoutExpired:
        initialized = False
    if not initialized:
        cleanup(scratch)
        return None
    patch_file = scratch / "candidate.patch"
    try:
        patch_file.write_bytes(patch_text.encode("utf-8"))
    except OSError:
        cleanup(scratch)
        return None
    # git-style -p1, then plain -p0. Inside a real work tree git refuses absolute/.. paths, so a
    # (model-supplied) patch cannot escape the scratch dir.
    try:
        applied = (
            _git(dest, "apply", "-p1", str(patch_file), timeout=_remaining())
            or _git(dest, "apply", "-p0", str(patch_file), timeout=_remaining())
            or _git(
                dest, "apply", "--ignore-space-change", "-p1", str(patch_file),
                timeout=_remaining(),
            )
            or _git(
                dest, "apply", "--ignore-space-change", "-p0", str(patch_file),
                timeout=_remaining(),
            )
        )
    except subprocess.TimeoutExpired:
        applied = False
    if applied:
        patch_file.unlink(missing_ok=True)
        return dest
    cleanup(scratch)
    return None


def cleanup(scratch: Path) -> None:
    """Remove a scratch dir returned by ``materialize_candidate`` (or its parent). Best-effort."""
    root = Path(scratch)
    target = root.parent if root.name == "repo" else root
    if "pebra-candidate-" in target.name:
        shutil.rmtree(target, ignore_errors=True)
