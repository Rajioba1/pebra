"""Shared graph-evidence trust predicate."""

from pebra.core.graph_trust import is_trusted_fanin
from pebra.core.models import FanInEvidence


def test_fresh_location_fanin_is_trusted() -> None:
    assert is_trusted_fanin(FanInEvidence(resolution_method="location", graph_freshness="fresh"))


def test_parse_error_graph_is_not_trusted_for_risk_injection() -> None:
    ev = FanInEvidence(
        resolution_method="location",
        graph_freshness="fresh",
        graph_file_error_count=1,
        symbol_fan_in_percentile=0.95,
        symbol_caller_count=12,
    )

    assert not is_trusted_fanin(ev)
