"""engine_paths — locate the graph engine (codegraph) launcher. Pure stdlib (os/shutil/pathlib).

`find_engine()` lookup order: PEBRA_CODEGRAPH_BIN override (a bin DIR or the launcher FILE) -> PATH
(shutil.which) -> PEBRA's MANAGED install for the pinned default version. This lets `pebra setup-graph`
make PEBRA ready WITHOUT a persistent PATH edit (a ratified non-goal on Windows): a later shell's
`pebra assess` still finds the managed binary even when it isn't on PATH.

Single source of truth for the managed install location — `cli.setup_graph` installs into
`managed_install_root(version)` and the resolver looks there, so the two can't drift.

Lives in `core` (stdlib only) so the adapter, the CLI surface, and tests all import it without breaching
import-linter. NOTE: step 3 probes only the PINNED default-version managed install — a non-default
`--version` managed install must be reached via PEBRA_CODEGRAPH_BIN or PATH (and the assess-path range
check would mark a non-accepted version untrusted anyway).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION

_ENGINE = "codegraph"


def managed_install_root(version: str) -> Path:
    """The dir `pebra setup-graph` extracts a standalone codegraph bundle into (shared seam)."""
    return Path.home() / ".codegraph" / "pebra" / version


def _is_windows() -> bool:
    # indirection so tests can flip platform WITHOUT patching the global os.name (which would make
    # pathlib build a PosixPath on Windows and crash).
    return os.name == "nt"


def _launcher_names() -> tuple[str, ...]:
    # mirrors the installer's probe order: .cmd then .exe on Windows; bare name on POSIX
    return (f"{_ENGINE}.cmd", f"{_ENGINE}.exe") if _is_windows() else (_ENGINE,)


def _launcher_in(bindir: Path) -> str | None:
    for name in _launcher_names():
        cand = bindir / name
        if cand.is_file():
            return str(cand)
    return None


def find_engine() -> str | None:
    """Locate the codegraph launcher (full path) or None. Pure lookup — never spawns."""
    override = os.environ.get("PEBRA_CODEGRAPH_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        if p.is_dir():
            hit = _launcher_in(p)
            if hit:
                return hit
        # misconfigured override -> fall through to PATH / managed install
    found = shutil.which(_ENGINE)
    if found:
        return found
    return _launcher_in(managed_install_root(CODEGRAPH_DEFAULT_VERSION) / "bin")
