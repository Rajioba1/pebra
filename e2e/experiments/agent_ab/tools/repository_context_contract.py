"""Blinded, provider-neutral repository Understand tool contract."""

from __future__ import annotations

from typing import Any

TOOL_NAME = "repository_context"
TOOL_DESCRIPTION = (
    "Retrieve bounded repository context before designing a significant or unfamiliar change. "
    "Provide the task or symbol and any relevant repository-relative file hints."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Task, concept, or symbol to understand.",
        },
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional repository-relative file hints.",
        },
    },
    "required": [],
}
OUTPUT_KEYS = (
    "status",
    "context",
    "related_files",
    "related_tests",
    "warnings",
    "truncated",
)

_MAX_CONTEXT_BYTES = 12_000
_MAX_PATHS = 32
_MAX_WARNINGS = 8


def _strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item][:limit]


def normalize_output(raw: object) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    status = value.get("status")
    if status not in {"available", "unavailable", "error"}:
        status = "unavailable"
    context = value.get("context") if isinstance(value.get("context"), str) else ""
    encoded = context.encode("utf-8")
    truncated = value.get("truncated") is True
    if len(encoded) > _MAX_CONTEXT_BYTES:
        context = encoded[:_MAX_CONTEXT_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    return {
        "status": status,
        "context": context,
        "related_files": _strings(value.get("related_files"), limit=_MAX_PATHS),
        "related_tests": _strings(value.get("related_tests"), limit=_MAX_PATHS),
        "warnings": _strings(value.get("warnings"), limit=_MAX_WARNINGS),
        "truncated": truncated,
    }
