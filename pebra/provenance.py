"""Source provenance — which PEBRA is running: version, install mode, and (for a source checkout) the
git short hash.

This answers "did I launch the released wheel or my working checkout?". It shells out to git AT MOST
once, and only for an editable install; callers must compute it once at startup and never on a hot path
(e.g. never on the dashboard's 5-second refresh).
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
from pathlib import Path

_DIST = "pebra"


def version() -> str:
    try:
        return importlib.metadata.version(_DIST)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


def is_editable() -> bool:
    """True for an editable (`pip install -e`) install, via the PEP 610 direct_url.json marker."""
    try:
        raw = importlib.metadata.distribution(_DIST).read_text("direct_url.json")
    except (importlib.metadata.PackageNotFoundError, OSError):
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return False
    # Valid JSON need not be an object (a corrupt/foreign direct_url.json could be null/list/number);
    # degrade to False rather than raising AttributeError on .get().
    if not isinstance(info, dict):
        return False
    dir_info = info.get("dir_info")
    return bool(isinstance(dir_info, dict) and dir_info.get("editable"))


def git_short_hash() -> str | None:
    """The checkout's short commit, or None if not a git checkout / git unavailable. Runs git ONCE."""
    root = Path(__file__).resolve().parent.parent  # <repo>/pebra/provenance.py -> <repo>
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def provenance_line(*, prefix: bool = True) -> str:
    """A one-line provenance string, e.g. "PEBRA 0.1.0 · editable · 0357a22" (prefix=True) or
    "0.1.0 · installed" (prefix=False, for use next to an existing "PEBRA" label)."""
    editable = is_editable()
    head = f"PEBRA {version()}" if prefix else version()
    parts = [head, "editable" if editable else "installed"]
    if editable:
        short = git_short_hash()
        if short:
            parts.append(short)
    return " · ".join(parts)
