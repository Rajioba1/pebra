"""patch_materializer (P0) — the shared "apply a patch to a throwaway tree" recipe.

Generalized from ``radon_adapter._apply_patch`` so both radon (benefit-delta measurement) and the
CodeGraph materialized-diff tier reuse one apply/read-back implementation. It NEVER touches the real
repo: it git-inits a temp dir, seeds the caller-supplied before-content, applies the patch VERBATIM,
and reads the after-content back.

Contract (load-bearing for the candidate-verification hash bind): the patch is applied as the exact
string the caller holds — the after-tree therefore corresponds to that exact patch, so hashing the same
string binds the materialization. Fail CLOSED: any git-init / apply failure returns ``None`` (never a
partial materialization). A ``None`` before-value = "file did not exist before" (not seeded, so a
creating patch can add it); a ``None`` after-value = "the patch deleted it".

Adapter layer: uses tempfile/subprocess (forbidden in core, allowed here). Path-escape protection comes
for free — ``git apply`` inside a real work tree refuses absolute / ``..`` paths, so a (possibly
model-supplied) patch cannot write outside the temp dir.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath


def _unsafe_rel(rel: str) -> bool:
    """True if ``rel`` is absolute, drive-anchored, or contains a ``..`` traversal on EITHER separator.
    The seed/read-back steps use ``root / rel`` with plain ``write_text``/``read_text`` (NOT ``git
    apply``), so git's own path-escape refusal does not protect them — this does. The drive check is
    load-bearing: a Windows DRIVE-RELATIVE key like ``"D:evil.py"`` is NOT ``is_absolute()`` and has no
    ``..``, yet ``root / "D:evil.py"`` escapes to another drive. Fail closed on anything unsafe."""
    if PureWindowsPath(rel).drive:  # drive-absolute ("C:\\x") OR drive-relative ("D:x") OR UNC
        return True
    if ":" in rel:
        return True
    for cls in (PurePosixPath, PureWindowsPath):
        p = cls(rel)
        if p.is_absolute() or ".." in p.parts:
            return True
    return False


def _git_init(cwd: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "init", "-q"], cwd=str(cwd), capture_output=True, text=True, timeout=30
        )
        if res.returncode != 0:
            return False
        res = subprocess.run(
            ["git", "config", "core.autocrlf", "false"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def _git_apply(cwd: Path, patch_file: Path) -> bool:
    """Apply inside the temp work tree, git-style ``-p1`` then plain ``-p0``. No ``--unsafe-paths``: git
    refuses absolute / ``..`` paths in a real work tree, so a patch cannot escape the temp dir."""
    for strip in ("-p1", "-p0"):
        try:
            res = subprocess.run(
                ["git", "apply", strip, str(patch_file)],
                cwd=str(cwd), capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if res.returncode == 0:
            return True
    return False


def materialize_patch(
    before: Mapping[str, str | None], patch: str
) -> dict[str, str | None] | None:
    """Apply ``patch`` to a throwaway copy of ``before`` and return the after-content per file.

    ``before[rel]`` = the current content to seed (or ``None`` = the file did not exist before, so it is
    not seeded). Returns ``{rel: after_content_or_None}`` for every key in ``before``; whole result is
    ``None`` iff git-init or the apply failed (fail-closed). The "changed nothing" policy is the
    caller's, not enforced here."""
    # Reject unsafe keys BEFORE any filesystem write — `root / "../x"` would escape the temp dir at
    # write_text time (git apply only guards the patch's OWN paths, not our seed/read-back).
    if any(_unsafe_rel(rel) for rel in before):
        return None
    with tempfile.TemporaryDirectory(prefix="pebra-materialize-") as td:
        scratch = Path(td)
        root = scratch / "repo"
        root.mkdir()
        if not _git_init(root):
            return None
        for rel, content in before.items():
            if content is None:
                continue  # did not exist before -> let a creating patch add it
            fp = root / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(content.encode("utf-8"))
        patch_file = scratch / "patch.diff"
        patch_file.write_bytes(patch.encode("utf-8"))
        if not _git_apply(root, patch_file):
            return None
        after: dict[str, str | None] = {}
        for rel in before:
            fp = root / rel
            try:
                after[rel] = fp.read_bytes().decode("utf-8", errors="replace") if fp.is_file() else None
            except OSError:
                after[rel] = None
        return after
