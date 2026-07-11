"""Pure matching for hidden task edit envelopes.

Entries ending in ``/`` denote a repository-relative subtree; all other entries denote exact files.
The first entry remains the task's primary target for advisory-adherence attribution.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath, PureWindowsPath


def normalize_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_in_scope(path: str, scope: Iterable[str]) -> bool:
    candidate = normalize_repo_path(path)
    posix = PurePosixPath(candidate)
    windows = PureWindowsPath(candidate)
    if (
        not candidate
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or any(":" in part for part in posix.parts)
    ):
        return False
    for raw in scope:
        pattern = normalize_repo_path(raw)
        if pattern.endswith("/"):
            if candidate.startswith(pattern):
                return True
        elif candidate == pattern:
            return True
    return False
