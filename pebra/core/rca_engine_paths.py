"""rca_engine_paths — locate the rust-code-analysis-cli benefit engine. Pure stdlib (os/shutil/pathlib).

``find_rca()`` lookup order: PEBRA_RCA_BIN override (a bin DIR or the launcher FILE) -> PATH
(shutil.which). Deliberately NARROWER than ``engine_paths.find_engine`` — only 2 tiers, no managed
install: there is no ``pebra setup-rca`` command, so nothing populates a managed dir for RCA. A user
installs the binary via ``cargo install --git https://github.com/mozilla/rust-code-analysis
rust-code-analysis-cli`` (crates.io's v0.0.25 does not compile against current tree-sitter) and it lands
on PATH (``~/.cargo/bin``).

Unlike codegraph (an npm ``.cmd`` shim), rust-code-analysis-cli is a NATIVE binary (``.exe`` on Windows,
bare on POSIX), so ``resolve_engine_argv`` wraps it trivially (its ``.cmd``/``.bat`` branch is a no-op).

Lives in ``core`` (stdlib only) so the adapter and any CLI surface import it without breaching the
import-linter.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_ENGINE = "rust-code-analysis-cli"
RCA_ACCEPTED_VERSION = "0.0.25"
RCA_SOURCE_REVISION = "37e5d83c056c8cbf827223d5814a93c5218df1a9"


def _is_windows() -> bool:
    # indirection so tests can flip platform without patching os.name (which would break pathlib).
    return os.name == "nt"


def _launcher_names() -> tuple[str, ...]:
    return (f"{_ENGINE}.exe",) if _is_windows() else (_ENGINE,)


def _launcher_in(bindir: Path) -> str | None:
    for name in _launcher_names():
        cand = bindir / name
        if cand.is_file():
            return str(cand)
    return None


def find_rca() -> str | None:
    """Locate the rust-code-analysis-cli binary (full path) or None. Pure lookup — never spawns."""
    override = os.environ.get("PEBRA_RCA_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            hit = _launcher_in(p)
            if hit:
                return hit
        # misconfigured override -> fall through to PATH
    return shutil.which(_ENGINE)
