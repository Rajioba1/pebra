"""Shared atomic JSON artifact writer for run-directory outputs. Pure stdlib; NEVER imports pebra.

tmp-write-then-replace so a reader (e.g. the run observatory polling the run dir) never sees a
half-written file, and a crash mid-write never leaves a corrupt artifact. Used for the additive
observability artifacts (coverage.json, run_status.json) and the crash-survivable outcomes.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write ``payload`` as indented JSON to ``path`` atomically (tmp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
