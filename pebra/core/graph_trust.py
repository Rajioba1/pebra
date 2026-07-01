"""Shared trust predicates for graph-derived evidence."""

from __future__ import annotations

from pebra.core.models import FanInEvidence

_TRUSTED_FANIN_RESOLUTION_METHODS = {"location", "name_fallback"}


def is_trusted_fanin(fanin: FanInEvidence | None) -> bool:
    """True when fan-in evidence came from a fresh, unambiguous graph resolution."""
    return (
        fanin is not None
        and fanin.graph_freshness == "fresh"
        and fanin.resolution_method in _TRUSTED_FANIN_RESOLUTION_METHODS
    )
