"""Convert exact structured replacements into a deterministic, read-only unified diff."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pebra.adapters._paths import is_safe_relative


@dataclass(frozen=True)
class CandidatePatch:
    patch: str
    expected_files: tuple[str, ...]


def build_candidate_patch(repo_root: Path | str, edits: Iterable[dict[str, Any]]) -> CandidatePatch:
    """Apply exact replacements in memory and return their canonical multi-file patch."""
    root = Path(repo_root).resolve()
    originals: dict[str, str] = {}
    updated: dict[str, str] = {}
    order: list[str] = []

    for edit in edits:
        rel = str(edit.get("path", "")).replace("\\", "/")
        if not is_safe_relative(str(root), rel):
            raise ValueError(f"unsafe candidate edit path: {rel!r}")
        old = edit.get("old_string")
        new = edit.get("new_string")
        if not isinstance(old, str) or not old:
            raise ValueError("candidate edit old_string must be non-empty")
        if not isinstance(new, str):
            raise ValueError("candidate edit new_string must be a string")
        if rel not in originals:
            path = root / rel
            if not path.is_file():
                raise ValueError(f"candidate edit file does not exist: {rel}")
            originals[rel] = path.read_text(encoding="utf-8")
            updated[rel] = originals[rel]
            order.append(rel)
        count = updated[rel].count(old)
        replace_all = edit.get("replace_all") is True
        if count != 1 and not (replace_all and count > 0):
            raise ValueError(f"candidate edit old_string matched {count} times in {rel}")
        updated[rel] = updated[rel].replace(old, new, -1 if replace_all else 1)

    if not order:
        raise ValueError("candidate edits must contain at least one edit")

    chunks: list[str] = []
    for rel in order:
        if originals[rel] == updated[rel]:
            raise ValueError(f"candidate edit made no change in {rel}")
        diff = "".join(difflib.unified_diff(
            originals[rel].splitlines(keepends=True),
            updated[rel].splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        ))
        chunks.append(f"diff --git a/{rel} b/{rel}\n{diff}")
    return CandidatePatch(patch="".join(chunks), expected_files=tuple(order))
