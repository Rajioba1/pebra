"""Pure, trustworthy identity projection for persisted assessment history."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import datetime
import hashlib
import json
import re
from typing import Any, Literal

from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


TargetProvenance = Literal[
    "candidate_bound", "declared", "legacy_guidance", "legacy_graph", "unavailable"
]
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class AssessmentHistoryIdentity:
    task: str | None
    action_id: str | None
    declared_files: tuple[str, ...]
    bound_files: tuple[str, ...]
    target_files: tuple[str, ...]
    target_provenance: TargetProvenance
    candidate_fingerprint: str | None
    assessed_at: str | None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _utf8_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _label(value: object) -> str | None:
    return value if isinstance(value, str) and value and _utf8_encodable(value) else None


def _assessed_at(value: object) -> str | None:
    timestamp = _label(value)
    if timestamp is None:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        return None
    return timestamp


def _paths(values: object, *, allow_file_path_records: bool = False) -> tuple[str, ...]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
        return ()
    paths: list[str] = []
    for value in values:
        if allow_file_path_records and isinstance(value, Mapping):
            value = value.get("file_path")
        if (
            not isinstance(value, str)
            or not value
            or "::" in value
            or not _utf8_encodable(value)
        ):
            continue
        normalized = value.replace("\\", "/")
        if normalized not in paths:
            paths.append(normalized)
    return tuple(paths)


def _candidate(binding: object) -> tuple[tuple[str, ...], str | None]:
    if not isinstance(binding, dict) or binding.get("algorithm") != CANDIDATE_BINDING_ALGORITHM:
        return (), None
    files = binding.get("files")
    if not isinstance(files, dict) or not files:
        return (), None
    if not all(
        isinstance(path, str)
        and bool(path)
        and "::" not in path
        and _utf8_encodable(path)
        and isinstance(digest, str)
        and _DIGEST_RE.fullmatch(digest) is not None
        for path, digest in files.items()
    ):
        return (), None
    canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return (), None
    return _paths(files.keys()), fingerprint


def project_assessment_identity(content: Mapping[str, Any]) -> AssessmentHistoryIdentity:
    """Project trustworthy display identity from one hash-covered assessment payload."""
    request = _mapping(content.get("request"))
    packet = _mapping(content.get("model_guidance_packet"))
    binding = _mapping(packet.get("binding"))
    bound_files, candidate_fingerprint = _candidate(binding.get("candidate"))

    revision_envelope = _mapping(request.get("revision_envelope"))
    declared_scope_present = "expected_files" in revision_envelope
    declared_files = _paths(revision_envelope.get("expected_files"))

    safe_scope = _mapping(binding.get("safe_scope"))
    legacy_guidance = list(_paths(safe_scope.get("files")))
    for value in _paths(safe_scope.get("symbols"), allow_file_path_records=True):
        if value not in legacy_guidance:
            legacy_guidance.append(value)

    scores = _mapping(content.get("scores"))
    symbol_scope = _mapping(scores.get("symbol_scope_evidence"))
    symbol_fanin = _mapping(symbol_scope.get("symbol_fanin"))
    legacy_graph = _paths(symbol_fanin.get("resolved_file_paths"))

    target_files: tuple[str, ...]
    target_provenance: TargetProvenance
    if bound_files:
        target_files, target_provenance = bound_files, "candidate_bound"
    elif declared_scope_present:
        target_files = declared_files
        target_provenance = "declared" if declared_files else "unavailable"
    elif legacy_guidance:
        target_files, target_provenance = tuple(legacy_guidance), "legacy_guidance"
    elif legacy_graph:
        target_files, target_provenance = legacy_graph, "legacy_graph"
    else:
        target_files, target_provenance = (), "unavailable"

    return AssessmentHistoryIdentity(
        task=_label(request.get("task")),
        action_id=_label(request.get("action_id")),
        declared_files=declared_files,
        bound_files=bound_files,
        target_files=target_files,
        target_provenance=target_provenance,
        candidate_fingerprint=candidate_fingerprint,
        assessed_at=_assessed_at(content.get("assessed_at")),
    )
