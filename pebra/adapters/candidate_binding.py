"""Bind an assessed patch to the normalized file contents a host edit would produce.

Assessment and host tools use different wire formats: assess receives a patch, while Claude exposes
Edit/Write/MultiEdit and Codex exposes apply_patch.  Comparing resulting file contents gives them one
candidate identity without weakening the comparison to repository, commit, and path alone.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pebra.adapters.patch_header_adapter import touched_files
from pebra.adapters.patch_materializer import materialize_patch
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.models import CandidateAction

_BASELINE_ALGORITHM = "sha256-git-worktree-v1"
_CODEX_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$")
_UNSUPPORTED_MODE = re.compile(
    r"^(?:old mode|new mode)\s+|^new file mode\s+(?!100644\s*$)", re.MULTILINE
)


def _has_unsupported_metadata(patch: str) -> bool:
    # v1 binds normalized contents only. Reject executable/special mode mutations instead of
    # pretending they are represented by the content digest.
    return bool(_UNSUPPORTED_MODE.search(patch))


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _digest(text: str | None) -> str:
    payload = b"\x00deleted" if text is None else b"\x01text\x00" + _normal(text).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_rel(repo_root: Path, value: str, *, base_dir: Path | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    windows = PureWindowsPath(value)
    if not candidate.is_absolute() and not windows.is_absolute() and (windows.drive or ":" in value):
        return None
    try:
        absolute = (
            candidate.resolve()
            if candidate.is_absolute()
            else ((base_dir or repo_root) / candidate).resolve()
        )
        rel = absolute.relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    if not rel or ".." in PurePosixPath(rel).parts:
        return None
    return os.path.normcase(rel).replace("\\", "/")


def _read(repo_root: Path, rel: str) -> str | None:
    path = repo_root / rel
    try:
        return path.read_bytes().decode("utf-8", errors="replace") if path.is_file() else None
    except OSError:
        return None


def _binding(after: dict[str, str | None]) -> dict[str, Any]:
    return {
        "algorithm": CANDIDATE_BINDING_ALGORITHM,
        "files": {rel: _digest(after[rel]) for rel in sorted(after)},
    }


def _codex_blocks(patch: str) -> list[tuple[str, str, list[str]]] | None:
    lines = patch.splitlines()
    if (
        not lines
        or lines[0] != "*** Begin Patch"
        or lines[-1] != "*** End Patch"
        or lines.count("*** Begin Patch") != 1
        or lines.count("*** End Patch") != 1
    ):
        return None
    blocks: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in lines[1:-1]:
        match = _CODEX_FILE.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            current = (match.group(1).lower(), match.group(2), [])
        elif line.startswith("*** ") or current is None:
            return None
        elif current is not None:
            current[2].append(line)
    if current is not None:
        blocks.append(current)
    return blocks or None


def _replace_once(text: str, old: str, new: str, *, replace_all: bool = False) -> str | None:
    count = text.count(old)
    if count == 0 or (not replace_all and count != 1):
        return None
    return text.replace(old, new, -1 if replace_all else 1)


def _apply_codex_update(before: str, lines: list[str]) -> str | None:
    text = _normal(before)
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        chunks.append(current)
    if not chunks:
        return None
    for chunk in chunks:
        old_lines: list[str] = []
        new_lines: list[str] = []
        for line in chunk:
            if line.startswith("+"):
                new_lines.append(line[1:])
            elif line.startswith("-"):
                old_lines.append(line[1:])
            else:
                value = line[1:] if line.startswith(" ") else line
                old_lines.append(value)
                new_lines.append(value)
        old = "\n".join(old_lines)
        new = "\n".join(new_lines)
        replaced = _replace_once(text, old, new)
        if replaced is None:
            return None
        text = replaced
    return text


def _materialize_codex(
    repo_root: Path, patch: str, *, base_dir: Path | None = None
) -> dict[str, str | None] | None:
    blocks = _codex_blocks(patch)
    if blocks is None:
        return None
    after: dict[str, str | None] = {}
    for kind, raw_path, lines in blocks:
        rel = _safe_rel(repo_root, raw_path, base_dir=base_dir)
        if rel is None or rel in after:
            return None
        before = _read(repo_root, rel)
        if kind == "add":
            if before is not None or any(not line.startswith("+") for line in lines):
                return None
            after[rel] = "\n".join(line[1:] for line in lines) + ("\n" if lines else "")
        elif kind == "delete":
            if before is None:
                return None
            after[rel] = None
        else:
            if before is None:
                return None
            updated = _apply_codex_update(before, lines)
            if updated is None:
                return None
            after[rel] = updated
    return after


def _materialize_candidate_patch(
    repo_root: str | Path, patch: str | None, *, base_dir: Path | None = None
) -> dict[str, str | None] | None:
    if not isinstance(patch, str) or not patch.strip() or _has_unsupported_metadata(patch):
        return None
    root = Path(repo_root).resolve()
    has_codex_syntax = any(line.startswith("*** ") for line in patch.splitlines())
    if has_codex_syntax:
        return _materialize_codex(root, patch, base_dir=base_dir)
    raw_paths = touched_files(patch)
    if not raw_paths:
        return None
    rels = [_safe_rel(root, path, base_dir=base_dir) for path in raw_paths]
    if any(rel is None for rel in rels):
        return None
    before: dict[str, str | None] = {}
    for rel in rels:
        if rel is None:
            continue
        content = _read(root, rel)
        before[rel] = _normal(content) if content is not None else None
    apply_dir = "."
    if base_dir is not None:
        try:
            apply_dir = base_dir.resolve().relative_to(root).as_posix() or "."
        except (OSError, ValueError):
            return None
    after = materialize_patch(before, _normal(patch), apply_dir=apply_dir)
    return after


def materialize_candidate_patch(
    repo_root: str | Path, patch: str | None
) -> dict[str, str | None] | None:
    """Return the exact normalized after-content used by candidate binding."""
    try:
        return _materialize_candidate_patch(repo_root, patch)
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        return None


def _binding_for_patch(
    repo_root: str | Path, patch: str | None, *, base_dir: Path | None = None
) -> dict[str, Any] | None:
    after = _materialize_candidate_patch(repo_root, patch, base_dir=base_dir)
    return _binding(after) if after is not None else None


def binding_for_patch(repo_root: str | Path, patch: str | None) -> dict[str, Any] | None:
    """Return a candidate binding, or None for malformed/unencodable/unmaterializable input."""
    try:
        return _binding_for_patch(repo_root, patch)
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        return None


def _binding_for_event(event: dict[str, Any], repo_root: str | Path) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    root = Path(repo_root).resolve()
    raw_cwd = event.get("cwd")
    event_cwd = Path(raw_cwd).resolve() if isinstance(raw_cwd, str) and raw_cwd else root
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if tool == "apply_patch":
        command = tool_input.get("command")
        if not isinstance(command, str) or _has_unsupported_metadata(command):
            return None
        return _binding_for_patch(root, command, base_dir=event_cwd)

    rel = _safe_rel(root, tool_input.get("file_path", ""), base_dir=event_cwd)
    if rel is None:
        return None
    before = _read(root, rel)
    if tool == "Write":
        content = tool_input.get("content")
        return _binding({rel: content}) if isinstance(content, str) else None
    if before is None:
        return None
    if tool == "Edit":
        old = tool_input.get("old_string")
        new = tool_input.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        after = _replace_once(before, old, new, replace_all=tool_input.get("replace_all") is True)
        return _binding({rel: after}) if after is not None else None
    if tool == "MultiEdit":
        after = before
        edits = tool_input.get("edits")
        if not isinstance(edits, list) or not edits:
            return None
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
            replaced = _replace_once(after, old, new, replace_all=edit.get("replace_all") is True)
            if replaced is None:
                return None
            after = replaced
        return _binding({rel: after})
    return None


def binding_for_event(event: dict[str, Any], repo_root: str | Path) -> dict[str, Any] | None:
    """Materialize a host edit; malformed model-controlled input is unverifiable, never exceptional."""
    try:
        return _binding_for_event(event, repo_root)
    except (OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        return None


def baseline_binding_for_action(
    action: CandidateAction, repo_root: str | Path
) -> dict[str, Any] | None:
    """Bind the complete non-ignored Git working-tree state before the candidate is applied."""
    root = Path(repo_root).resolve()
    for value in action.expected_files:
        if _safe_rel(root, value) is None:
            return None
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "--full-index", "HEAD", "--"],
            cwd=root,
            capture_output=True,
            timeout=15,
            check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode != 0 or untracked.returncode != 0:
        return None
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\x00")
    digest.update(diff.stdout)
    digest.update(b"\x00untracked\x00")
    for raw in sorted(value for value in untracked.stdout.split(b"\x00") if value):
        try:
            rel_value = os.fsdecode(raw)
        except UnicodeError:
            return None
        rel = _safe_rel(root, rel_value)
        if rel is None:
            return None
        path = root / rel
        try:
            content = path.read_bytes()
        except OSError:
            return None
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(content).digest())
    return {"algorithm": _BASELINE_ALGORITHM, "digest": digest.hexdigest()}


class CandidateBindingAdapter:
    """Production adapter used by assess composition."""

    def bind_candidate(self, action: CandidateAction, repo_root: str) -> dict[str, Any] | None:
        return binding_for_patch(repo_root, action.proposed_patch)

    def bind_baseline(self, action: CandidateAction, repo_root: str) -> dict[str, Any] | None:
        return baseline_binding_for_action(action, repo_root)
