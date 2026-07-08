"""Shared trust predicates for graph-derived evidence."""

from __future__ import annotations

from pebra.core.models import FanInEvidence

_TRUSTED_FANIN_RESOLUTION_METHODS = {"location", "name_fallback"}


def is_trusted_fanin(fanin: FanInEvidence | None) -> bool:
    """True when fan-in evidence came from a fresh, unambiguous, parse-clean graph resolution."""
    return (
        fanin is not None
        and fanin.graph_freshness == "fresh"
        and fanin.resolution_method in _TRUSTED_FANIN_RESOLUTION_METHODS
        and fanin.graph_file_error_count == 0
    )


def effective_impact_percentile(fanin: FanInEvidence) -> float:
    """The strongest [0,1] graph-reach percentile for a fan-in: the max of the direct, structural-modify,
    and transitive-modify percentiles (each counted only when its OWN supporting count is > 0). Monotonic
    — more reach in any channel raises it. Single source of truth shared by the MODIFY-risk term and the
    benefit-exposure weight, so both read the same 'how much graph reach does this change have' number."""
    direct = fanin.symbol_fan_in_percentile if fanin.symbol_caller_count > 0 else 0.0
    structural = fanin.modify_impact_percentile if fanin.modify_impact_count > 0 else 0.0
    transitive = (
        fanin.modify_transitive_impact_percentile
        if fanin.modify_transitive_impact_count > 0
        else 0.0
    )
    return max(direct, structural, transitive)
