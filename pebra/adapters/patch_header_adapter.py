"""patch_header_adapter — parse git unified-diff ``diff --git`` HEADER blocks for file-level operations.

Pure stdlib regex, no I/O. Distinct from hunk-body parsing (codegraph_adapter.parse_old_side_ranges):
this reads the per-file header (deleted/new file mode, rename from/to, similarity index) to classify
the file operation. Ordinary modify patches produce no entry, so the assess path stays inert (and the
golden snapshot byte-identical) for normal edits.

RENAME vs MOVE is decided by whether the parent directory changed: same parent = RENAME (in-place),
different parent = MOVE. ``kind`` values are FileOperationKind values (DELETE/CREATE/RENAME/MOVE).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_DIFF_GIT = re.compile(r"^diff --git a/(.*) b/(.*)$")
_RENAME_FROM = re.compile(r"^rename from (.*)$")
_RENAME_TO = re.compile(r"^rename to (.*)$")
_SIMILARITY = re.compile(r"^similarity index (\d+)%$")


@dataclass(frozen=True)
class DestructiveOp:
    kind: str  # FileOperationKind value: "DELETE" | "CREATE" | "RENAME" | "MOVE"
    old_path: str | None
    new_path: str | None
    similarity_index: int | None = None


def _classify(block: list[str], a_path: str, b_path: str) -> DestructiveOp | None:
    deleted = any(line.startswith("deleted file mode") for line in block)
    created = any(line.startswith("new file mode") for line in block)
    rename_from = rename_to = None
    similarity = None
    for line in block:
        if (m := _RENAME_FROM.match(line)):
            rename_from = m.group(1)
        elif (m := _RENAME_TO.match(line)):
            rename_to = m.group(1)
        elif (m := _SIMILARITY.match(line)):
            similarity = int(m.group(1))

    if deleted:
        return DestructiveOp(kind="DELETE", old_path=a_path, new_path=None)
    if created:
        return DestructiveOp(kind="CREATE", old_path=None, new_path=b_path)
    if rename_from is not None or rename_to is not None:
        old_p = rename_from if rename_from is not None else a_path
        new_p = rename_to if rename_to is not None else b_path
        same_dir = PurePosixPath(old_p).parent == PurePosixPath(new_p).parent
        return DestructiveOp(
            kind="RENAME" if same_dir else "MOVE",
            old_path=old_p, new_path=new_p, similarity_index=similarity,
        )
    return None  # ordinary modify — no file-level op


def parse_patch_headers(patch: str) -> list[DestructiveOp]:
    """Return one DestructiveOp per file with a delete/create/rename/move op. Modify files yield none."""
    if not patch:
        return []
    ops: list[DestructiveOp] = []
    a_path = b_path = None
    block: list[str] = []

    def _flush() -> None:
        if a_path is not None and (op := _classify(block, a_path, b_path)) is not None:
            ops.append(op)

    for line in patch.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            _flush()
            a_path, b_path = m.group(1), m.group(2)
            block = []
        elif a_path is not None:
            block.append(line)
    _flush()
    return ops
