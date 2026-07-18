"""ScoreSparklines — compact trend lines for RAU, expected loss, and benefit (Observatory TUI M4).

Built on Textual's Sparkline. A sparkline shows shape and relative magnitude over recent assessments —
NOT an axis or a zero reference line, so the copy never implies one. Each row shows the latest value plus
the min/max of the window. Values are the persisted score projections, oldest -> newest left to right.
"""

from __future__ import annotations

import math
from typing import Any

from textual.containers import Horizontal, Vertical
from textual.widgets import Label, Sparkline

# (score key, display label). Order = reading order top to bottom.
_TREND_KEYS: tuple[tuple[str, str], ...] = (
    ("rau", "RAU"),
    ("expected_loss", "Expected loss"),
    ("benefit", "Benefit"),
)


def trend_values(scores_series: list[dict[str, Any]], key: str) -> list[float]:
    """Finite values for one score key in chronological order (the series is newest-first)."""
    values: list[float] = []
    for item in reversed(scores_series):
        value = (item.get("scores") or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            values.append(float(value))
    return values


def trend_summary(values: list[float]) -> str:
    """Latest value + window min/max. No axis or zero-line is claimed."""
    if not values:
        return "—"
    return f"now {values[-1]:+.2f}   min {min(values):+.2f}   max {max(values):+.2f}"


class ScoreSparklines(Vertical):
    def compose(self):
        for key, label in _TREND_KEYS:
            with Horizontal(classes="trend-row"):
                yield Label(label, classes="trend-label")
                yield Sparkline(id=f"spark-{key}", classes="trend-spark")
                yield Label("—", id=f"summary-{key}", classes="trend-summary")

    def update_series(self, scores_series: list[dict[str, Any]]) -> None:
        for key, _label in _TREND_KEYS:
            values = trend_values(scores_series, key)
            # Sparkline needs >= 1 point to render; an empty window stays blank with a "—" summary.
            self.query_one(f"#spark-{key}", Sparkline).data = values or None
            self.query_one(f"#summary-{key}", Label).update(trend_summary(values))
