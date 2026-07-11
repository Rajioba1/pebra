"""Pure cumulative file-level evidence for multi-file destructive candidates."""

from __future__ import annotations

import math

from pebra.core.models import FileFanInRollup

_BREADTH_CAP = 0.08


def aggregate_file_rollups(rollups: list[FileFanInRollup]) -> FileFanInRollup:
    if not rollups:
        return FileFanInRollup(file_count=0)
    trusted = [r for r in rollups if r.resolution_method != "unresolved"]
    if not trusted:
        return FileFanInRollup(
            file_count=len(rollups),
            fallback_reason=f"{len(rollups)} of {len(rollups)} file rollups unresolved",
        )
    hottest = max(
        trusted,
        key=lambda r: (r.file_symbol_fanin_rollup_percentile, r.distinct_caller_count),
    )
    callers = {caller for rollup in trusted for caller in rollup.caller_node_ids}
    max_known_callers = max((r.distinct_caller_count for r in trusted), default=0)
    breadth = min(
        _BREADTH_CAP,
        0.02 * math.log2(max(1, len(rollups))),
    )
    unresolved = len(rollups) - len(trusted)
    return FileFanInRollup(
        max_caller_count=max((r.max_caller_count for r in trusted), default=0),
        distinct_caller_count=max(max_known_callers, len(callers)),
        symbol_count=sum(r.symbol_count for r in trusted),
        file_symbol_fanin_rollup_percentile=hottest.file_symbol_fanin_rollup_percentile,
        resolution_method="file_location",
        graph_freshness=hottest.graph_freshness,
        fallback_reason=(
            f"{unresolved} of {len(rollups)} file rollups unresolved" if unresolved else None
        ),
        caller_node_ids=tuple(sorted(callers)),
        file_count=len(rollups),
        cumulative_breadth_bonus=breadth,
    )
