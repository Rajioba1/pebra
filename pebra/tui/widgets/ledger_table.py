"""Ledger rendering helpers (Observatory TUI M3).

The signature element: the RAU lane — a fixed [-1.0, +1.0] mini-track with the gate (RAU=0, engine Gate 4)
at center, so the ledger physically shows edits falling left (held/rejected) or right (proceed) of the
gate. It encodes RAU only; the decision is a separate glyph/label column. The numeric RAU (format_rau)
is authoritative and never clamped — only the visual marker clamps, with an overflow arrow at the edge.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from textual.content import Content

from pebra.tui.theme import VERDICT_PALETTE, verdict_for

# Semantic column keys and their breakpoint-specific ordering. A data refresh keeps the current set;
# only a width-breakpoint crossing rebuilds it.
WIDE_COLUMNS = (
    "assessment_id",
    "target",
    "task",
    "assessed_commit",
    "gate_lane",
    "decision",
    "rau",
    "expected_loss",
    "benefit",
    "status",
    "assessed_at",
)
NORMAL_COLUMNS = (
    "assessment_id", "target", "assessed_commit", "decision", "rau", "status",
)
NARROW_COLUMNS = ("assessment_id", "target", "decision", "rau")

LEDGER_LABELS = {
    "assessment_id": "ID",
    "target": "target",
    "task": "task",
    "assessed_commit": "assessed commit",
    "gate_lane": "gate lane",
    "decision": "decision",
    "rau": "RAU",
    "expected_loss": "expected loss",
    "benefit": "benefit",
    "status": "status",
    "assessed_at": "assessed time",
}

# The gate-lane track width in the ledger. Deliberately narrower than render_rau_lane's default (13) so
# all eight columns fit ~80 cols: the lane is a coarse visual cue and the authoritative value is the
# separate `rau` column, so fewer track cells lose no real precision.
LEDGER_LANE_WIDTH = 9

_MARKER = "●"           # RAU position marker — deliberately NOT any decision glyph
_GATE = "│"             # the gate axis at RAU = 0
_TRACK = "·"
_OVERFLOW_LEFT = "«"    # RAU below the -1.0 window (marker clamps; the number does not)
_OVERFLOW_RIGHT = "»"   # RAU above the +1.0 window

# Textual 8 cannot measure native Content cells for DataTable auto-width (it reports one cell), so
# these two semantic columns must be explicit or the lane and full verdict labels are clipped.
LEDGER_COLUMN_WIDTHS = {
    "target": 18,
    "task": 28,
    "assessed_commit": len(LEDGER_LABELS["assessed_commit"]),
    "gate_lane": LEDGER_LANE_WIDTH,
    "decision": max(Content(f"{v.glyph} {v.label}").cell_length for v in VERDICT_PALETTE.values()),
    "expected_loss": len(LEDGER_LABELS["expected_loss"]),
    "assessed_at": 16,
}


def columns_for_width(width: int) -> tuple[str, ...]:
    if width >= 120:
        return WIDE_COLUMNS
    if width >= 80:
        return NORMAL_COLUMNS
    return NARROW_COLUMNS


def render_rau_lane(rau: float | None, *, width: int = 13) -> str:
    """A fixed-width [-1, +1] track. In range: a marker at RAU's position. Out of range: an overflow
    arrow at the edge (never the marker). Missing/NaN: gate only, no marker."""
    cells = [_TRACK] * width
    center = width // 2
    cells[center] = _GATE

    if rau is None or math.isnan(rau):
        return "".join(cells)
    if math.isinf(rau) or rau < -1.0 or rau > 1.0:
        cells[-1 if rau > 0 else 0] = _OVERFLOW_RIGHT if rau > 0 else _OVERFLOW_LEFT
        return "".join(cells)

    index = max(0, min(width - 1, round((rau + 1.0) / 2.0 * (width - 1))))
    # Only an exactly-zero RAU sits on the gate cell. A small nonzero RAU that rounds onto center is
    # nudged one cell toward its sign, so the lane never hides which side of the gate an edit fell —
    # the near-boundary region is the most important to read. The number (format_rau) stays exact.
    if index == center and rau != 0.0:
        index = max(0, min(width - 1, center + (1 if rau > 0 else -1)))
    cells[index] = _MARKER
    return "".join(cells)


def format_rau(rau: float | None) -> str:
    """The authoritative RAU value, signed to two decimals. Missing/non-finite render as an em dash.
    Never clamped — an out-of-window RAU still prints its true value."""
    if rau is None or not math.isfinite(rau):
        return "—"
    return f"{rau:+.2f}"


def format_target(paths: Sequence[str]) -> str:
    """Compact a normalized target list without inventing missing history."""
    if not paths:
        return "target unavailable"
    first = PurePosixPath(paths[0]).name or paths[0]
    return first if len(paths) == 1 else f"{first} +{len(paths) - 1}"


def format_assessed_at(value: str | None) -> str:
    """Compact the validated UTC timestamp projected by the read model."""
    return value[:16].replace("T", " ") if value else "—"


def format_task(value: str | None, *, width: int = 28) -> str:
    """Bound task display text; callers retain the untouched history row."""
    text = " ".join((value or "").split())
    if not text:
        return "—"
    return text if len(text) <= width else f"{text[: width - 1]}…"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _fmt_score(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}" if number is not None and math.isfinite(number) else "—"


def short_commit(commit: Any) -> str:
    return commit[:7] if isinstance(commit, str) and commit else "—"


def decision_cell(decision: str, *, dark: bool = True) -> Content:
    """A colored 'glyph label' cell for a decision (e.g. '▸ Proceed'). Color is one channel; the glyph
    and full label are the others, so the verdict is legible without color."""
    verdict = verdict_for(decision)
    return Content(f"{verdict.glyph} {verdict.label}").stylize(
        verdict.color_dark if dark else verdict.color_light
    )


def ledger_row(
    assessment: Mapping[str, Any],
    *,
    columns: Sequence[str] = WIDE_COLUMNS,
    dark: bool = True,
    group_size: int = 1,
) -> tuple[Any, ...]:
    """One display-only row from a controller summary, projected into the active column set."""
    scores = assessment.get("scores") or {}
    rau = _num(scores.get("rau"))
    cells = {
        "assessment_id": (
            f"{assessment.get('assessment_id', '—')} ×{group_size}"
            if group_size > 1
            else assessment.get("assessment_id", "—")
        ),
        "target": format_target(assessment.get("target_files") or ()),
        "task": format_task(assessment.get("task")),
        "assessed_commit": short_commit(assessment.get("assessed_commit")),
        "gate_lane": Content(render_rau_lane(rau, width=LEDGER_LANE_WIDTH)),
        "decision": decision_cell(str(assessment.get("decision", "")), dark=dark),
        "rau": format_rau(rau),
        "expected_loss": _fmt_score(scores.get("expected_loss")),
        "benefit": _fmt_score(scores.get("benefit")),
        "status": assessment.get("terminal_status") or "pending",
        "assessed_at": format_assessed_at(assessment.get("assessed_at")),
    }
    return tuple(cells[column] for column in columns)
