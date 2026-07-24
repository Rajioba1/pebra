"""Pure matching of persisted structural-feature payloads to learned-fact scopes."""

from __future__ import annotations

import fnmatch
from typing import Any


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def scope_matches_features(
    scope_kind: str,
    scope_value: str,
    scope_json: dict[str, Any],
    features: dict[str, Any],
) -> bool:
    """Match one immutable prediction-feature payload without importing the learning engine."""
    if scope_kind == "global":
        return True
    sym = features.get("symbol") or {}
    structural = features.get("structural") or {}
    domains = (features.get("domain") or {}).get("matched_domains") or []
    if scope_kind == "action_type":
        return sym.get("action_type") == scope_value
    if scope_kind == "path_glob":
        file_path = sym.get("file_path")
        return bool(file_path) and fnmatch.fnmatch(file_path, scope_value)
    if scope_kind == "symbol":
        return sym.get("symbol_id") == scope_value
    if scope_kind == "public_api":
        return bool(sym.get("is_public_api"))
    if scope_kind == "public_api_domain":
        return bool(sym.get("is_public_api")) and scope_json.get("domain") in domains
    if scope_kind == "domain":
        return scope_value in domains
    if scope_kind == "domain_change_kind":
        return (
            scope_json.get("domain") in domains
            and scope_json.get("change_kind") == sym.get("change_kind")
        )
    if scope_kind == "high_symbol_fan_in":
        threshold = _float(scope_json.get("min_percentile", 0.90), 0.90)
        return bool(structural.get("is_high_symbol_fan_in")) or _float(
            structural.get("symbol_fan_in_percentile", 0.0)
        ) >= threshold
    if scope_kind == "domain_high_symbol_fan_in":
        threshold = _float(scope_json.get("min_percentile", 0.90), 0.90)
        return scope_json.get("domain") in domains and _float(
            structural.get("symbol_fan_in_percentile", 0.0)
        ) >= threshold
    return False
