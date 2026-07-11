from pebra.core.file_risk_aggregation import aggregate_file_rollups
from pebra.core.models import FileFanInRollup


def _rollup(callers: tuple[str, ...], percentile: float) -> FileFanInRollup:
    return FileFanInRollup(
        distinct_caller_count=len(callers),
        max_caller_count=len(callers),
        symbol_count=1,
        file_symbol_fanin_rollup_percentile=percentile,
        resolution_method="file_location",
        graph_freshness="fresh",
        caller_node_ids=callers,
    )


def test_single_file_rollup_is_score_identity() -> None:
    original = _rollup(("a", "b"), 0.6)

    result = aggregate_file_rollups([original])

    assert result.file_symbol_fanin_rollup_percentile == 0.6
    assert result.cumulative_breadth_bonus == 0.0
    assert result.file_count == 1


def test_multifile_rollup_unions_callers_and_adds_bounded_breadth() -> None:
    result = aggregate_file_rollups([
        _rollup(("a", "b"), 0.6),
        _rollup(("b", "c"), 0.5),
    ])

    assert result.distinct_caller_count == 3
    assert result.file_symbol_fanin_rollup_percentile == 0.6
    assert 0.0 < result.cumulative_breadth_bonus <= 0.08
    assert result.file_count == 2


def test_unresolved_file_does_not_erase_trusted_file_risk() -> None:
    result = aggregate_file_rollups([
        _rollup(("a",), 0.5),
        FileFanInRollup(fallback_reason="missing graph"),
    ])

    assert result.resolution_method == "file_location"
    assert result.file_symbol_fanin_rollup_percentile == 0.5
    assert result.file_count == 2
    assert result.fallback_reason == "1 of 2 file rollups unresolved"


def test_missing_caller_identities_cannot_reduce_known_caller_count() -> None:
    result = aggregate_file_rollups([
        FileFanInRollup(
            distinct_caller_count=10,
            file_symbol_fanin_rollup_percentile=0.7,
            resolution_method="file_location",
            graph_freshness="fresh",
        ),
        _rollup(("known",), 0.4),
    ])

    assert result.distinct_caller_count == 10
