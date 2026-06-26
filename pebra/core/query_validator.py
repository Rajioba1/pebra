"""query_validator (Architecture §3) — pure: validates pebra_explain / pebra_compare query dicts.

Phase-2 minimal contract: requires a non-empty ``assessment_id``. Full schema validation (date
ranges, comparison pairs) lands with the MCP/explain surfaces in a later phase. No I/O.
"""

from __future__ import annotations

from typing import Any


class QueryValidationError(ValueError):
    """Raised when an explain/compare query is structurally invalid."""


def validate_query(raw: dict[str, Any]) -> dict[str, Any]:
    aid = raw.get("assessment_id")
    if not isinstance(aid, str) or not aid:
        raise QueryValidationError("assessment_id is required and must be a non-empty string")
    return raw
