"""rca_probe — boundary-safe stdlib twin of ``pebra.core.rca_engine_paths.find_rca``.

The e2e tree may not ``import pebra`` (boundary discipline), yet several e2e lanes must decide whether
the rust-code-analysis-cli benefit engine is present using the SAME lookup order the production CLI will
use — otherwise a skip predicate can drift from what the subprocess actually resolves. This is the single
shared copy; ``tests/unit/test_rca_probe_parity.py`` pins it to production ``find_rca`` so it cannot drift.

Lookup order (mirrors find_rca): PEBRA_RCA_BIN override (launcher FILE or bin DIR) -> PATH (shutil.which).
A misconfigured override falls through to PATH. Pure stdlib.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_ENGINE = "rust-code-analysis-cli"


def _launcher_names() -> tuple[str, ...]:
    return (f"{_ENGINE}.exe",) if os.name == "nt" else (_ENGINE,)


def find_rca() -> str | None:
    """Locate the rust-code-analysis-cli binary (full path) or None. Pure lookup — never spawns."""
    override = os.environ.get("PEBRA_RCA_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            for name in _launcher_names():
                cand = p / name
                if cand.is_file():
                    return str(cand)
        # misconfigured override (missing file / dir without launcher) -> fall through to PATH
    return shutil.which(_ENGINE)
