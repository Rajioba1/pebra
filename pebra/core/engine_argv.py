"""resolve_engine_argv — build a runnable argv for the external graph engine CLI. Pure stdlib.

Windows hazard (found in A2 real-binary validation): codegraph (and npm) install as a `.cmd` shim, not
a `.exe`. Python's subprocess CANNOT spawn a `.cmd` by BARE name (it doesn't apply PATHEXT without a
shell -> FileNotFoundError), and spawning a `.cmd` by full path under shell=False is fragile on CPython
3.12.4+ (the gh-99781 / CVE-2024-3219 `.bat`/`.cmd` argument-injection guard can raise). So we:
  1. resolve the tool via shutil.which (honors PATHEXT -> returns the real `.CMD` path on Windows), and
  2. on Windows, run a resolved `.cmd`/`.bat` through `cmd /c` (cmd.exe handles `.cmd` quoting natively).
Never `shell=True`. Lives in `core` (stdlib `os`/`shutil` only — not on the impure-libs banlist) so BOTH
the adapter and the CLI surface can import it without breaching import-linter.
"""

from __future__ import annotations

import os
import shutil


def resolve_engine_argv(exe: str, args: list[str]) -> list[str]:
    """Return a runnable argv for ``exe`` + ``args``.

    ``exe`` may be a BARE tool name (resolved on PATH via shutil.which) or a FULL path (used as-is).
    A resolved Windows ``.cmd``/``.bat`` is wrapped as ``["cmd", "/c", path, *args]`` — the only
    reliable way to spawn it without ``shell=True``. A bare name that is NOT on PATH is returned
    unresolved so ``subprocess`` raises ``FileNotFoundError`` naturally (preserving the
    'engine absent -> degrade' contract callers already rely on).
    """
    is_bare = not (os.path.isabs(exe) or "/" in exe or "\\" in exe)
    resolved = shutil.which(exe) if is_bare else exe
    if resolved is None:
        return [exe, *args]  # not on PATH -> let subprocess fail with FileNotFoundError
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved, *args]
    return [resolved, *args]
