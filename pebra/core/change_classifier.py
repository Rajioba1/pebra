"""Change classifier (Architecture §5, AD-27) — pure, stdlib only.

Receives parsed ``SymbolDiff`` rows (dicts) plus thresholds and returns the change-kind summary.
The adapter (``ast_diff_adapter``) owns parsing/I/O; this module is a pure function of its inputs.

Symbol/scope evidence is canonical assessment evidence, not only a high-risk filter: it feeds
ordinary ``p_event``, Affected Area, review cost, guidance, and learning buckets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pebra.core.constants import ChangeKind

# Severity ranking for max_change_kind. UNKNOWN sits high (conservative fallback) but below the
# kinds we can positively identify as contract/side-effect.
_SEVERITY: dict[ChangeKind, int] = {
    ChangeKind.COSMETIC: 0,
    ChangeKind.TEST_ONLY: 1,
    ChangeKind.DIRECTIVE: 2,
    ChangeKind.BEHAVIORAL: 3,
    ChangeKind.UNKNOWN: 4,
    ChangeKind.CONTRACT: 5,
    ChangeKind.SIDE_EFFECT: 6,
}

# Kinds that can qualify as a consequential symbol change (given a consequential context).
_CONSEQUENTIAL_KINDS = {
    ChangeKind.BEHAVIORAL,
    ChangeKind.CONTRACT,
    ChangeKind.SIDE_EFFECT,
    ChangeKind.DIRECTIVE,
    ChangeKind.UNKNOWN,
}

_DEFAULT_FAN_IN_PERCENTILE = 0.90


def severity(kind: ChangeKind) -> int:
    """Public severity rank for a ChangeKind (higher = more severe). Used by guardrail drift checks."""
    return _SEVERITY.get(kind, _SEVERITY[ChangeKind.UNKNOWN])


@dataclass(frozen=True)
class ChangeSummary:
    max_change_kind: str
    changed_symbols: list[str]
    visibility: str
    consequential_symbol_changed: bool
    consequence_reason: list[str] = field(default_factory=list)
    fallback_reason: str | None = None


def classify_symbol(row: dict[str, Any]) -> ChangeKind:
    """Classify one parsed SymbolDiff row into a ChangeKind (most-severe matching rule wins)."""
    if (
        row.get("payment_api_changed")
        or row.get("db_write_changed")
        or row.get("migration_changed")
        or row.get("external_side_effect_changed")
    ):
        return ChangeKind.SIDE_EFFECT
    if row.get("identity_replacement_suspected"):
        # same name + same signature but the body was wholly replaced (M4): treat as a contract-level
        # change — the symbol now means something different than the pre-edit packet approved.
        return ChangeKind.CONTRACT
    if (
        row.get("signature_changed")
        or row.get("return_shape_changed")
        or (row.get("visibility") in {"exported", "public_api"} and row.get("body_changed"))
    ):
        return ChangeKind.CONTRACT
    if row.get("body_changed") or row.get("control_flow_changed"):
        return ChangeKind.BEHAVIORAL
    if row.get("directive_comment_changed"):
        return ChangeKind.DIRECTIVE
    if row.get("test_only"):
        return ChangeKind.TEST_ONLY
    # Truly cosmetic only when nothing semantic changed and we actually parsed it.
    return ChangeKind.COSMETIC


def _is_consequential(
    row: dict[str, Any], kind: ChangeKind, fan_in_threshold: float
) -> tuple[bool, list[str]]:
    if kind not in _CONSEQUENTIAL_KINDS:
        return False, []
    reasons: list[str] = []
    if row.get("visibility") in {"exported", "public_api"}:
        reasons.append(f"visibility={row.get('visibility')}")
    if row.get("callers_percentile", 0.0) >= fan_in_threshold:
        reasons.append(f"callers_percentile>={fan_in_threshold}")
    if row.get("transitive_reaches_consequence_symbol"):
        reasons.append("transitive_reaches_consequence_symbol")
    for flag in (
        "external_side_effect_changed",
        "db_write_changed",
        "payment_api_changed",
        "migration_changed",
    ):
        if row.get(flag):
            reasons.append(f"{flag}=true")
    return (len(reasons) > 0), reasons


def classify_diff(rows: list[dict[str, Any]], thresholds: dict[str, float]) -> ChangeSummary:
    """Summarize a set of parsed SymbolDiff rows (AD-27 Layer-1 evidence)."""
    fan_in_threshold = thresholds.get(
        "consequential_symbol_fan_in_percentile", _DEFAULT_FAN_IN_PERCENTILE
    )
    if not rows:
        return ChangeSummary(
            max_change_kind=ChangeKind.UNKNOWN.value,
            changed_symbols=[],
            visibility="unknown",
            consequential_symbol_changed=False,
            consequence_reason=[],
            fallback_reason="no parsed symbol rows; fall back to file/path-level risk",
        )

    max_kind = ChangeKind.COSMETIC
    consequential = False
    reasons: list[str] = []
    for row in rows:
        kind = classify_symbol(row)
        if _SEVERITY[kind] > _SEVERITY[max_kind]:
            max_kind = kind
        is_conseq, row_reasons = _is_consequential(row, kind, fan_in_threshold)
        if is_conseq:
            consequential = True
            reasons.extend(row_reasons)

    # representative visibility = the most severe row's visibility (first matching max kind)
    visibility = next(
        (r.get("visibility", "unknown") for r in rows if classify_symbol(r) == max_kind),
        "unknown",
    )
    return ChangeSummary(
        max_change_kind=max_kind.value,
        changed_symbols=[r.get("symbol_id", "?") for r in rows],
        visibility=visibility,
        consequential_symbol_changed=consequential,
        consequence_reason=list(dict.fromkeys(reasons)),  # dedupe, keep order
        fallback_reason=None,
    )
