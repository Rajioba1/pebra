"""dashboard_metrics — pure aggregation for the Risk Observatory calibration view.

Turns a flat list of ``(predicted_probability, observed_binary)`` pairs into the per-bin reliability
diagram the dashboard charts. This is the diagram companion to ``learning_eval.ece`` (which reduces the
same binning to a single scalar): identical equal-width partition on ``[0, 1]`` with ``p == 1.0`` in the
final bin, so the weighted per-bin |observed − predicted| sums back to ECE (pinned by test).

Distinct from ``ece`` in one deliberate way: the dashboard must NEVER raise. An empty pair list (a repo
with no labelled predictions yet) returns empty bins, not a ``ValueError`` — a fail-soft read surface.

Pure stdlib; core-clean (imports only ``pebra.core``).
"""

from __future__ import annotations

import bisect
import math
from typing import Any


def reliability_bins(pairs: list[tuple[float, int]], n_bins: int = 10) -> list[dict[str, Any]]:
    """Equal-width reliability bins over ``[0, 1]``.

    Returns exactly ``n_bins`` dicts (including empty ones, so the chart has a stable x-axis), each:
    ``{predicted_lo, predicted_hi, count, observed_rate, mean_predicted}``. ``observed_rate`` and
    ``mean_predicted`` are ``None`` for an empty bin. Binning mirrors ``learning_eval.ece``: boundaries
    at ``i/n_bins``, ``bisect_right`` placement, ``p == 1.0`` in the last bin.
    """
    if n_bins <= 0:
        raise ValueError(f"n_bins must be > 0, got {n_bins}")
    step = 1.0 / n_bins
    edges = [i * step for i in range(1, n_bins)]
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        if not math.isfinite(p) or p < 0.0 or p > 1.0 or y not in (0, 1):
            continue
        buckets[bisect.bisect_right(edges, p)].append((p, y))
    out: list[dict[str, Any]] = []
    for i, b in enumerate(buckets):
        count = len(b)
        out.append(
            {
                "predicted_lo": i * step,
                "predicted_hi": (i + 1) * step,
                "count": count,
                "observed_rate": (sum(y for _, y in b) / count) if count else None,
                "mean_predicted": (sum(p for p, _ in b) / count) if count else None,
            }
        )
    return out
