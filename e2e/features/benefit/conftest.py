"""RCA benefit e2e lane — CLI-boundary only (NO ``import pebra``; boundary discipline).

Gated on the ``rust-code-analysis-cli`` binary being present, detected via stdlib ``shutil.which`` (this
re-implements ``find_rca``'s PEBRA_RCA_BIN -> PATH lookup WITHOUT importing pebra). ``require_rca`` is a
plain (non-autouse) fixture so the fail-safe test — which must pass with OR without the binary — can opt
out of it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def rca_binary() -> str | None:
    """Locate rust-code-analysis-cli without importing pebra: PEBRA_RCA_BIN (file/dir) -> PATH."""
    override = os.environ.get("PEBRA_RCA_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            names = ("rust-code-analysis-cli.exe",) if os.name == "nt" else ("rust-code-analysis-cli",)
            for name in names:
                if (p / name).is_file():
                    return str(p / name)
    return shutil.which("rust-code-analysis-cli")


@pytest.fixture
def require_rca() -> None:
    if rca_binary() is None:
        pytest.skip("rust-code-analysis-cli not installed (cargo install --git .../rust-code-analysis)")
