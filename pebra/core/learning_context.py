"""Pure, append-only historical learning-context values.

This module deliberately builds only display/recall records.  It has no scoring,
gate, sanction, or storage dependency; historical prose is data, never instructions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from typing import Any, Literal, Mapping

from pebra.core.assessment_history import project_assessment_identity


SYMBOL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HASH_VERSION = 1
MAX_TARGET_FILES = 16
MAX_SYMBOLS = 16


@dataclass(frozen=True)
class LearningContextEntry:
    learning_context_id: str
    repo_id: str
    assessment_id: str
    task: str
    action_id: str
    target_files: tuple[str, ...]
    symbols: tuple[str, ...]
    assessed_commit: str | None
    candidate_fingerprint: str | None
    decision: str
    expected_loss: float | None
    benefit: float | None
    expected_utility: float | None
    utility_sd: float | None
    rau: float | None
    terminal_status: str
    verification_summary: str
    measured_benefit: float | None
    lesson: str
    source_assessment_hash: str
    source_outcome_hash: str
    created_at: str
    row_hash: str


@dataclass(frozen=True)
class LearningContextRecall:
    status: Literal["available", "empty", "unavailable", "corrupt"]
    entries: tuple[LearningContextEntry, ...]
    file_hints: tuple[str, ...]
    symbol_hints: tuple[str, ...]
    warnings: tuple[str, ...]
    truncated: bool


def is_valid_symbol(value: object) -> bool:
    return isinstance(value, str) and SYMBOL_PATTERN.fullmatch(value) is not None


def literal_fts_query(value: object) -> str:
    """Return literal lexical terms, never FTS syntax supplied by a caller."""
    if not isinstance(value, str):
        return ""
    terms = re.findall(r"[A-Za-z0-9_]+", value)
    return " ".join(f'"{term}"' for term in terms[:32])


def canonical_entry_content(entry: LearningContextEntry, previous_hash: str) -> str:
    content = {
        "learning_context_id": entry.learning_context_id,
        "repo_id": entry.repo_id,
        "assessment_id": entry.assessment_id,
        "task": entry.task,
        "action_id": entry.action_id,
        "target_files": entry.target_files,
        "symbols": entry.symbols,
        "assessed_commit": entry.assessed_commit,
        "candidate_fingerprint": entry.candidate_fingerprint,
        "decision": entry.decision,
        "expected_loss": entry.expected_loss,
        "benefit": entry.benefit,
        "expected_utility": entry.expected_utility,
        "utility_sd": entry.utility_sd,
        "rau": entry.rau,
        "terminal_status": entry.terminal_status,
        "verification_summary": entry.verification_summary,
        "measured_benefit": entry.measured_benefit,
        "lesson": entry.lesson,
        "source_assessment_hash": entry.source_assessment_hash,
        "source_outcome_hash": entry.source_outcome_hash,
        "created_at": entry.created_at,
        "hash_version": HASH_VERSION,
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(entry: LearningContextEntry, previous_hash: str) -> str:
    return hashlib.sha256((previous_hash + canonical_entry_content(entry, previous_hash)).encode("utf-8")).hexdigest()


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _summary(_guardrails: Mapping[str, Any]) -> str:
    # Never echo free-form persisted/caller-adjacent prose into recalled instructions.
    return "PEBRA verify proceeded"


def build_learning_context_entry(
    *,
    learning_context_id: str,
    assessment_id: str,
    content: Mapping[str, Any],
    assessment_hash: str,
    outcome_hash: str,
    outcome: Mapping[str, Any],
    guardrails: Mapping[str, Any] | None,
    created_at: str,
    previous_hash: str,
) -> LearningContextEntry | None:
    """Build one deterministic record only for a verified completed outcome.

    Caller outcome detail is intentionally ignored. Metrics come only from the
    persisted assessment and latest PEBRA verify record; free-form lesson/agent
    payloads cannot enter recall.
    """
    if not isinstance(guardrails, Mapping) or guardrails.get("pre_commit_decision") != "proceed":
        return None
    if outcome.get("terminal_status") != "completed":
        return None
    if (
        not isinstance(assessment_hash, str)
        or _HASH_PATTERN.fullmatch(assessment_hash) is None
        or not isinstance(outcome_hash, str)
        or _HASH_PATTERN.fullmatch(outcome_hash) is None
    ):
        return None
    repo_id = content.get("repo_id")
    if not isinstance(repo_id, str) or not repo_id:
        return None
    identity = project_assessment_identity(content)
    task = (identity.task or "")[:1024]
    action_id = (identity.action_id or "")[:256]
    if not task or not action_id:
        return None
    scores = content.get("scores") if isinstance(content.get("scores"), Mapping) else {}
    symbols: list[str] = []
    request = content.get("request") if isinstance(content.get("request"), Mapping) else {}
    revision = (
        request.get("revision_envelope")
        if isinstance(request.get("revision_envelope"), Mapping)
        else {}
    )
    obligations = (
        request.get("task_obligations")
        if isinstance(request.get("task_obligations"), Mapping)
        else {}
    )
    requested_symbols: list[object] = []
    for values in (
        obligations.get("required_symbols"), revision.get("public_symbols")
    ):
        if isinstance(values, (list, tuple)):
            requested_symbols.extend(values)
    symbols = sorted({
        value
        for value in requested_symbols
        if isinstance(value, str) and is_valid_symbol(value)
    })[:MAX_SYMBOLS]
    measured_benefit = _finite(guardrails.get("measured_benefit"))
    decision = content.get("decision") if isinstance(content.get("decision"), str) else ""
    lesson = f"Verified completed outcome for {task}; PEBRA decision was {decision or 'unknown'}."
    provisional = LearningContextEntry(
        learning_context_id=learning_context_id, repo_id=repo_id, assessment_id=assessment_id,
        task=task, action_id=action_id,
        target_files=tuple(sorted(dict.fromkeys(identity.target_files)))[:MAX_TARGET_FILES],
        symbols=tuple(symbols),
        assessed_commit=content.get("assessed_commit") if isinstance(content.get("assessed_commit"), str) else None,
        candidate_fingerprint=identity.candidate_fingerprint, decision=decision,
        expected_loss=_finite(scores.get("expected_loss")), benefit=_finite(scores.get("benefit")),
        expected_utility=_finite(scores.get("expected_utility")), utility_sd=_finite(scores.get("utility_sd")),
        rau=_finite(scores.get("rau")), terminal_status="completed", verification_summary=_summary(guardrails),
        measured_benefit=measured_benefit, lesson=lesson, source_assessment_hash=assessment_hash,
        source_outcome_hash=outcome_hash, created_at=created_at, row_hash="",
    )
    return replace(provisional, row_hash=entry_hash(provisional, previous_hash))
