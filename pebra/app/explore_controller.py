"""Recall verified history, then retrieve current repository structure.

Historical learning remains labelled data.  Only validated path and symbol identifiers may sharpen
the current graph query; prose, decisions, scores, and outcomes never cross that boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
import re
from typing import Literal

from pebra.core.constants import Decision
from pebra.core.exploration import (
    ExplorationResult,
    clamp_bounds,
    normalize_repository_files,
)
from pebra.core.learning_context import (
    LearningContextEntry,
    LearningContextRecall,
    is_valid_gate_identifier,
    is_valid_symbol,
)
from pebra.ports.learning_context_port import LearningContextPort
from pebra.ports.repository_explorer_port import RepositoryExplorer


MAX_RECALLED_LESSONS = 5
MAX_RECALLED_FILES = 16
MAX_RECALLED_SYMBOLS = 16
RECALL_BYTE_LIMIT = 4096
_NON_CURRENT_WARNING = (
    "Historical record cannot establish current repository truth; current repository context "
    "is unavailable."
)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LEARNING_ID_PATTERN = re.compile(r"^lc_[1-9][0-9]*$")
_ASSESSMENT_ID_PATTERN = re.compile(r"^asm_[1-9][0-9]*$")
_DECISIONS = frozenset(value.value for value in Decision)


@dataclass(frozen=True)
class KnowledgeExplorationResult:
    learning_context: LearningContextRecall
    repository_context: ExplorationResult


def _empty_recall(
    status: Literal["empty", "unavailable", "corrupt"], warning: str = ""
) -> LearningContextRecall:
    warnings = (warning,) if warning else ()
    return LearningContextRecall(status, (), (), (), warnings, False)


def _text(value: object, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or (not allow_empty and not value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _optional_text(value: object) -> bool:
    return value is None or _text(value)


def _finite_or_none(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _valid_entry(entry: LearningContextEntry, repo_root: str, repo_id: str) -> bool:
    required_text = (
        entry.repo_id, entry.task, entry.action_id, entry.decision,
        entry.terminal_status, entry.verification_summary, entry.lesson, entry.created_at,
    )
    if (
        any(not _text(value) for value in required_text)
        or entry.repo_id != repo_id
        or not isinstance(entry.learning_context_id, str)
        or _LEARNING_ID_PATTERN.fullmatch(entry.learning_context_id) is None
        or not isinstance(entry.assessment_id, str)
        or _ASSESSMENT_ID_PATTERN.fullmatch(entry.assessment_id) is None
        or entry.decision not in _DECISIONS
        or entry.terminal_status != "completed"
        or not _optional_text(entry.assessed_commit)
        or not (
            entry.candidate_fingerprint is None
            or (
                isinstance(entry.candidate_fingerprint, str)
                and _HASH_PATTERN.fullmatch(entry.candidate_fingerprint) is not None
            )
        )
        or not all(
            isinstance(value, str) and _HASH_PATTERN.fullmatch(value) is not None
            for value in (
                entry.source_assessment_hash, entry.source_outcome_hash, entry.row_hash
            )
        )
        or not all(
            _finite_or_none(value)
            for value in (
                entry.expected_loss, entry.benefit, entry.expected_utility,
                entry.utility_sd, entry.rau, entry.measured_benefit,
            )
        )
        or not isinstance(entry.target_files, tuple)
        or len(entry.target_files) > MAX_RECALLED_FILES
        or not isinstance(entry.symbols, tuple)
        or len(entry.symbols) > MAX_RECALLED_SYMBOLS
        or not isinstance(entry.gates_fired, tuple)
        or len(entry.gates_fired) > 16
        or not all(is_valid_symbol(value) for value in entry.symbols)
        or not all(is_valid_gate_identifier(value) for value in entry.gates_fired)
    ):
        return False
    for path in entry.target_files:
        if not _text(path) or "::" in path:
            return False
        if not normalize_repository_files(repo_root, (path,)):
            return False
    return True


def _recall_size(recall: LearningContextRecall) -> int:
    return len(
        json.dumps(asdict(recall), sort_keys=True, ensure_ascii=False).encode("utf-8")
    )


def _hints(
    repo_root: str, entries: tuple[LearningContextEntry, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    files = normalize_repository_files(
        repo_root, tuple(path for entry in entries for path in entry.target_files)
    )
    symbols = tuple(dict.fromkeys(symbol for entry in entries for symbol in entry.symbols))
    truncated = len(files) > MAX_RECALLED_FILES or len(symbols) > MAX_RECALLED_SYMBOLS
    return files[:MAX_RECALLED_FILES], symbols[:MAX_RECALLED_SYMBOLS], truncated


def _pack_warnings(
    status: Literal["available", "empty", "unavailable", "corrupt"],
    entries: tuple[LearningContextEntry, ...],
    files: tuple[str, ...],
    symbols: tuple[str, ...],
    warnings: tuple[str, ...],
) -> tuple[tuple[str, ...], bool]:
    selected: list[str] = []
    truncated = len(warnings) > 8
    for warning in warnings[:8]:
        candidate = LearningContextRecall(
            status, entries, files, symbols, tuple((*selected, warning)), False
        )
        if _recall_size(candidate) <= RECALL_BYTE_LIMIT:
            selected.append(warning)
        else:
            truncated = True
    return tuple(selected), truncated


def _sanitize_recall(
    recall: object, repo_root: str, repo_id: str
) -> LearningContextRecall:
    if not isinstance(recall, LearningContextRecall):
        return _empty_recall("corrupt", "learning-context result was malformed")
    if not isinstance(recall.status, str) or recall.status not in {
        "available", "empty", "unavailable", "corrupt"
    }:
        return _empty_recall("corrupt", "learning-context status was malformed")
    if type(recall.truncated) is not bool or not isinstance(recall.warnings, tuple):
        return _empty_recall("corrupt", "learning-context result was malformed")
    if not all(_text(value, allow_empty=True) for value in recall.warnings):
        return _empty_recall("corrupt", "learning-context warnings were malformed")
    if recall.status != "available":
        warnings, warnings_truncated = _pack_warnings(
            recall.status, (), (), (), recall.warnings
        )
        return LearningContextRecall(
            recall.status, (), (), (), warnings, recall.truncated or warnings_truncated
        )
    if not isinstance(recall.entries, tuple):
        return _empty_recall("corrupt", "learning-context entries were malformed")

    validated: list[LearningContextEntry] = []
    for raw_entry in recall.entries:
        if not isinstance(raw_entry, LearningContextEntry) or not _valid_entry(
            raw_entry, repo_root, repo_id
        ):
            return _empty_recall("corrupt", "learning-context entry was malformed")
        validated.append(raw_entry)

    entries: list[LearningContextEntry] = []
    truncated = recall.truncated or len(validated) > MAX_RECALLED_LESSONS
    for entry in validated:
        if len(entries) >= MAX_RECALLED_LESSONS:
            continue
        candidate_entries = tuple((*entries, entry))
        files, symbols, hints_truncated = _hints(repo_root, candidate_entries)
        candidate = LearningContextRecall(
            "available", candidate_entries, files, symbols, (), False
        )
        if _recall_size(candidate) <= RECALL_BYTE_LIMIT:
            entries.append(entry)
            truncated = truncated or hints_truncated
        else:
            truncated = True

    if not entries:
        warnings, warnings_truncated = _pack_warnings(
            "empty", (), (), (), recall.warnings
        )
        return LearningContextRecall(
            "empty", (), (), (), warnings, truncated or warnings_truncated
        )

    selected_entries = tuple(entries)
    files, symbols, hints_truncated = _hints(repo_root, selected_entries)
    warnings, warnings_truncated = _pack_warnings(
        "available", selected_entries, files, symbols, recall.warnings
    )
    return LearningContextRecall(
        "available",
        selected_entries,
        files,
        symbols,
        warnings,
        truncated or hints_truncated or warnings_truncated,
    )


def _refined_query(query: str, symbols: tuple[str, ...]) -> str:
    if not symbols:
        return query
    return f"{query}\n\nIdentifiers: {' '.join(symbols)}"


def explore_repository(
    repo_root: str,
    repo_id: str,
    query: str,
    *,
    learning_port: LearningContextPort | None,
    explorer: RepositoryExplorer,
    files: tuple[str, ...] = (),
    max_files: int = 8,
    max_bytes: int = 24_000,
) -> KnowledgeExplorationResult:
    """Return historical recall first and current repository context second."""
    if not isinstance(repo_id, str) or not repo_id.strip():
        recall = _empty_recall(
            "unavailable", "learning context unavailable: repository identity is ambiguous"
        )
    elif learning_port is None:
        recall = _empty_recall(
            "unavailable", "learning-context store unavailable"
        )
    else:
        try:
            recalled = learning_port.recall_learning_context(
                repo_id, query, byte_limit=RECALL_BYTE_LIMIT
            )
        except Exception:
            recalled = _empty_recall(
                "unavailable", "learning-context search unavailable"
            )
        recall = _sanitize_recall(recalled, repo_root, repo_id)

    original_files = normalize_repository_files(repo_root, files)
    recalled_files = recall.file_hints if recall.status == "available" else ()
    merged_files = tuple(dict.fromkeys((*original_files, *recalled_files)))
    symbols = recall.symbol_hints if recall.status == "available" else ()
    provider_query = _refined_query(query, symbols)
    max_files, max_bytes = clamp_bounds(max_files, max_bytes)
    snapshot = explorer.prepare(repo_root)
    repository = explorer.explore(
        repo_root,
        provider_query,
        snapshot=snapshot,
        files=merged_files,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    if repository.status != "available" and recall.entries:
        warnings = tuple(dict.fromkeys((*repository.warnings, _NON_CURRENT_WARNING)))
        repository = replace(repository, warnings=warnings)
    return KnowledgeExplorationResult(recall, repository)
