"""Pure extraction of repository-relative files declared by unified-diff headers."""

from __future__ import annotations

import shlex


def touched_files(patch: str) -> tuple[str, ...]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            continue
        if len(parts) != 4 or parts[:2] != ["diff", "--git"]:
            continue
        for raw in parts[2:]:
            path = raw[2:] if raw.startswith(("a/", "b/")) else raw
            if path and path != "/dev/null":
                paths.add(path.replace("\\", "/"))
    return tuple(sorted(paths))
