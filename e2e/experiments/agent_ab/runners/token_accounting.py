"""Provider-token accounting for the deterministic agent harness.

Provider usage applies to a complete model turn.  Missing counters stay unavailable rather than
being coerced to zero, because a partial provider response cannot support a token-efficiency claim.
"""

from __future__ import annotations

from typing import Any, Iterable

from e2e.experiments.agent_ab.runners.model_client import ModelTurn

_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_UNDERSTAND_SCOPE = "whole provider turns requesting or consuming repository_context"


def summarize(
    turns: Iterable[ModelTurn], *, label: str = "provider_token_usage"
) -> dict[str, Any]:
    """Return additive totals while preserving unavailable provider counters."""
    items = list(turns)
    result: dict[str, Any] = {
        "label": label,
        "scope": _UNDERSTAND_SCOPE if label == "understand_turn_usage" else "all provider turns",
        "turn_count": len(items),
    }
    for field in _FIELDS:
        values = [getattr(turn, field) for turn in items]
        result[field] = (
            sum(values)
            if values and all(type(value) is int and value >= 0 for value in values)
            else None
        )
    result["usage_complete"] = bool(items) and all(
        type(turn.input_tokens) is int
        and turn.input_tokens >= 0
        and type(turn.output_tokens) is int
        and turn.output_tokens >= 0
        for turn in items
    )
    return result


def aggregate(
    summaries: Iterable[dict[str, Any]], *, label: str = "provider_token_usage"
) -> dict[str, Any]:
    """Aggregate already-normalized run summaries without erasing missingness."""
    items = list(summaries)
    result: dict[str, Any] = {
        "label": label,
        "scope": _UNDERSTAND_SCOPE if label == "understand_turn_usage" else "all provider turns",
        "turn_count": sum(
            value if type(value) is int and value >= 0 else 0
            for value in (item.get("turn_count") for item in items)
        ),
    }
    for field in _FIELDS:
        values = [item.get(field) for item in items]
        result[field] = (
            sum(values)
            if values and all(type(value) is int and value >= 0 for value in values)
            else None
        )
    result["usage_complete"] = (
        bool(items)
        and result["input_tokens"] is not None
        and result["output_tokens"] is not None
        and all(item.get("usage_complete") is True for item in items)
    )
    return result
