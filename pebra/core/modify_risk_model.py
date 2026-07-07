"""modify_risk_model — graph-wide risk injection for MODIFY edits.

DELETE already has a file-roll-up model because removing a file destroys every symbol in it. This
module covers the sibling gap for ordinary MODIFY edits: changing a graph-important symbol can break
callers/implementers elsewhere in the codebase even when the changed file itself is tiny.

The model is deliberately an expected-loss input, not a hard gate. It returns ordinary event rows, so
the existing assessment math and Gate 3 decide. No trusted graph -> no graph bonus and no fabricated
"low risk" signal.

All numeric constants are prior_uncalibrated and intentionally conservative until calibrated by M5.
"""

from __future__ import annotations

from typing import Any

from pebra.core.constants import ChangeKind, UNCERTAIN_STRUCTURE_TIERS
from pebra.core.graph_trust import is_trusted_fanin
from pebra.core.models import ArchitectureEvidence, FanInEvidence, SymbolDiffEvidence

_P_EVENT_CAP = 0.45
_HIGH_FANIN_THRESHOLD = 0.90
_FANIN_BONUS_MAX = 0.28
_BASELINE_CONTRACT = 0.08
_BASELINE_PUBLIC_API = 0.12
_BASELINE_SCHEMA_OR_MIGRATION = 0.18
_C3_C4_BONUS = {"C3": 0.05, "C4": 0.08}
_ARCH_ENTRYPOINT_BONUS = 0.04
_LARGE_OWNER_SPAN_LINES = 60
_VERY_LARGE_OWNER_SPAN_LINES = 120
_LARGE_OWNER_BONUS = 0.06
_VERY_LARGE_OWNER_BONUS = 0.10
_MULTI_SYMBOL_BONUS_MAX = 0.05
_OUTGOING_EDGE_BONUS_MAX = 0.06
_OWNER_KIND_BONUS = 0.03
_RISKY_OWNER_KINDS = {"class", "interface", "trait", "protocol", "component", "route"}
_BASE_DISUTILITY_DEPENDENCY_BREAK = 0.60
_BASE_DISUTILITY_PUBLIC_API_BREAK = 0.70
_BASE_DISUTILITY_API_CONTRACT_BREAK = 0.70


def _event(name: str, p_event: float, disutility: float) -> dict[str, Any]:
    return {
        "event": name,
        "p_event": p_event,
        "elicited_disutility": disutility,
        "probability_source_type": "prior_uncalibrated",
        "disutility_source_type": "prior_uncalibrated",
        "risk_source": "graph_modify_risk",
    }


def _kind(sde: SymbolDiffEvidence) -> ChangeKind:
    try:
        return ChangeKind(sde.max_change_kind)
    except ValueError:
        return ChangeKind.UNKNOWN


def _is_public(sde: SymbolDiffEvidence) -> bool:
    return sde.visibility in {"public", "public_api", "exported"}


def _graph_public_contract(fanin: FanInEvidence) -> bool:
    return fanin.is_exported_contract or fanin.is_abstract_or_interface_contract


def _effective_impact_percentile(fanin: FanInEvidence) -> float:
    direct = fanin.symbol_fan_in_percentile if fanin.symbol_caller_count > 0 else 0.0
    structural = fanin.modify_impact_percentile if fanin.modify_impact_count > 0 else 0.0
    return max(direct, structural)


def _p_event(
    *,
    sde: SymbolDiffEvidence,
    fanin: FanInEvidence,
    arch: ArchitectureEvidence,
    criticality_stage: str,
    is_schema_change: bool,
    is_migration: bool,
) -> float:
    base = _BASELINE_PUBLIC_API if (_is_public(sde) or _graph_public_contract(fanin)) else _BASELINE_CONTRACT
    if is_schema_change or is_migration:
        base = max(base, _BASELINE_SCHEMA_OR_MIGRATION)
    if arch.domain_entrypoint or arch.architecture_anchor_score > 0.0:
        base += _ARCH_ENTRYPOINT_BONUS
    if fanin.max_owner_span_lines >= _VERY_LARGE_OWNER_SPAN_LINES:
        base += _VERY_LARGE_OWNER_BONUS
    elif fanin.max_owner_span_lines >= _LARGE_OWNER_SPAN_LINES:
        base += _LARGE_OWNER_BONUS
    if set(fanin.owner_kinds) & _RISKY_OWNER_KINDS:
        base += _OWNER_KIND_BONUS
    if fanin.resolved_symbol_count > 1:
        base += min(_MULTI_SYMBOL_BONUS_MAX, 0.02 * (fanin.resolved_symbol_count - 1))
    outgoing_total = sum(fanin.outgoing_edge_counts.values())
    if outgoing_total:
        base += min(_OUTGOING_EDGE_BONUS_MAX, 0.01 * outgoing_total)
    base += _C3_C4_BONUS.get(criticality_stage, 0.0)
    impact_percentile = _effective_impact_percentile(fanin)
    fanin_bonus = min(_FANIN_BONUS_MAX, max(0.0, impact_percentile) *
                      (_FANIN_BONUS_MAX / _HIGH_FANIN_THRESHOLD))
    return min(_P_EVENT_CAP, base + fanin_bonus)


def events_for_modify_risk(
    *,
    symbol_diff: SymbolDiffEvidence,
    fanin: FanInEvidence | None,
    arch: ArchitectureEvidence,
    criticality_stage: str,
    is_schema_change: bool = False,
    is_migration: bool = False,
) -> list[dict[str, Any]]:
    """Return graph-backed events for ordinary MODIFY edits.

    Scope is intentionally narrow:
    - only non-file-operation MODIFY edits (``file_operation_kind == "NONE"``)
    - only trusted graph fan-in
    - only contract/side-effect/unknown/high-fan-in consequential changes

    Low-fan-in ordinary body edits remain unchanged; unresolved graph evidence never contributes.
    """
    if symbol_diff.file_operation_kind != "NONE" or not is_trusted_fanin(fanin):
        return []
    assert fanin is not None  # narrowed by _trusted

    kind = _kind(symbol_diff)
    high_fanin = _effective_impact_percentile(fanin) >= _HIGH_FANIN_THRESHOLD
    large_owner = fanin.max_owner_span_lines >= _LARGE_OWNER_SPAN_LINES
    broad_symbol_edit = fanin.resolved_symbol_count > 1
    known_contractish = kind in {ChangeKind.CONTRACT, ChangeKind.SIDE_EFFECT}
    # A coarse codegraph_structural classification is still uncertain (owner touched, inner change
    # unseen), so it counts as an unknown change here — otherwise reclassifying UNKNOWN -> BEHAVIORAL
    # for an internal owner would silently drop the MODIFY dependency_break event this term feeds.
    unknown_change = (
        kind is ChangeKind.UNKNOWN or symbol_diff.structure_tier in UNCERTAIN_STRUCTURE_TIERS
    )
    public_consequential = _is_public(symbol_diff) and symbol_diff.consequential_symbol_changed
    graph_public_contract = _graph_public_contract(fanin)
    graph_important_modify = (high_fanin or large_owner or broad_symbol_edit) and (
        known_contractish
        or unknown_change
        or symbol_diff.consequential_symbol_changed
        or is_schema_change
        or is_migration
    )
    public_known_contract = _is_public(symbol_diff) and known_contractish
    graph_public_known_contract = graph_public_contract and known_contractish and high_fanin
    if (
        not graph_important_modify
        and not public_consequential
        and not public_known_contract
        and not graph_public_known_contract
    ):
        return []

    p_event = _p_event(
        sde=symbol_diff,
        fanin=fanin,
        arch=arch,
        criticality_stage=criticality_stage,
        is_schema_change=is_schema_change,
        is_migration=is_migration,
    )
    events = [_event("dependency_break", p_event, _BASE_DISUTILITY_DEPENDENCY_BREAK)]
    if _is_public(symbol_diff) or graph_public_contract:
        events.append(_event("public_api_break", p_event, _BASE_DISUTILITY_PUBLIC_API_BREAK))
    if is_schema_change or is_migration:
        events.append(_event("api_contract_break", p_event, _BASE_DISUTILITY_API_CONTRACT_BREAK))
    return events
