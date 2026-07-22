"""Recall verified history, then retrieve current repository structure.

Historical learning remains labelled data.  Only validated path and symbol identifiers may sharpen
the current graph query; prose, decisions, scores, and outcomes never cross that boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from typing import Literal

from pebra.core.exploration import (
    ExplorationResult,
    clamp_bounds,
    normalize_repository_files,
)
from pebra.core.learning_context import (
    LearningContextEntry,
    LearningContextRecall,
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


@dataclass(frozen=True)
class KnowledgeExplorationResult:
    learning_context: LearningContextRecall
    repository_context: ExplorationResult


def _empty_recall(
    status: Literal["empty", "unavailable", "corrupt"], warning: str = ""
) -> LearningContextRecall:
    warnings = (warning,) if warning else ()
    return LearningContextRecall(status, (), (), (), warnings, False)


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
    if recall.status != "available":
        warnings = tuple(
            value[:256]
            for value in recall.warnings[:8]
            if isinstance(value, str)
        )
        return LearningContextRecall(recall.status, (), (), (), warnings, recall.truncated)
    if not isinstance(recall.entries, tuple):
        return _empty_recall("corrupt", "learning-context entries were malformed")

    entries: list[LearningContextEntry] = []
    used = 0
    truncated = bool(recall.truncated)
    for raw_entry in recall.entries:
        if not isinstance(raw_entry, LearningContextEntry) or raw_entry.repo_id != repo_id:
            return _empty_recall("corrupt", "learning-context entry scope was malformed")
        try:
            size = len(
                json.dumps(asdict(raw_entry), sort_keys=True, ensure_ascii=False).encode("utf-8")
            )
        except (TypeError, ValueError, OverflowError, RecursionError):
            return _empty_recall("corrupt", "learning-context entry was malformed")
        if len(entries) >= MAX_RECALLED_LESSONS or used + size > RECALL_BYTE_LIMIT:
            truncated = True
            continue
        entries.append(raw_entry)
        used += size

    if not entries:
        return LearningContextRecall("empty", (), (), (), (), truncated)

    raw_files: list[str] = []
    raw_symbols: list[str] = []
    for entry in entries:
        if not isinstance(entry.target_files, tuple) or not isinstance(entry.symbols, tuple):
            return _empty_recall("corrupt", "learning-context identifiers were malformed")
        raw_files.extend(value for value in entry.target_files if isinstance(value, str))
        raw_symbols.extend(value for value in entry.symbols if is_valid_symbol(value))

    normalized_files = normalize_repository_files(repo_root, tuple(raw_files))
    unique_symbols = tuple(dict.fromkeys(raw_symbols))
    if len(normalized_files) > MAX_RECALLED_FILES or len(unique_symbols) > MAX_RECALLED_SYMBOLS:
        truncated = True
    warnings = tuple(
        value[:256]
        for value in recall.warnings[:8]
        if isinstance(value, str)
    )
    return LearningContextRecall(
        "available",
        tuple(entries),
        normalized_files[:MAX_RECALLED_FILES],
        unique_symbols[:MAX_RECALLED_SYMBOLS],
        warnings,
        truncated,
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
