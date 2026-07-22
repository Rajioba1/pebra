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

from pebra.core.constants import ActionStatus
from pebra.tui.theme import VERDICT_PALETTE, verdict_for

# The ledger is a complete audit instrument at every terminal width. Narrow terminals expose the
# rightmost fields by the DataTable's native horizontal scrolling; they must never disappear.
LEDGER_COLUMNS = (
    "assessment_id",
    "target",
    "decision",
    "rau",
    "expected_loss",
    "benefit",
    "status",
    "prior",
    "lesson",
    "task",
    "assessed_commit",
    "gate_lane",
    "assessed_at",
)

LEDGER_LABELS = {
    "assessment_id": "ID",
    "target": "target",
    "task": "task",
    "assessed_commit": "assessed commit",
    "gate_lane": "gate lane",
    "decision": "decision",
    "rau": "RAU",
    "expected_loss": "loss",
    "benefit": "benefit",
    "status": "status",
    "prior": "prior",
    "lesson": "lesson",
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
# Content-backed semantic columns need explicit widths or their full values may be clipped.
LEDGER_COLUMN_WIDTHS = {
    "target": 18,
    "task": 28,
    "assessed_commit": len(LEDGER_LABELS["assessed_commit"]),
    "gate_lane": LEDGER_LANE_WIDTH,
    "decision": max(Content(f"{v.glyph} {v.label}").cell_length for v in VERDICT_PALETTE.values()),
    "status": max(len(LEDGER_LABELS["status"]), *(len(status.value) for status in ActionStatus)),
    "assessed_at": 16,
}


def columns_for_width(width: int) -> tuple[str, ...]:
    """Return the immutable audit-column order; width only changes the visible viewport."""
    return LEDGER_COLUMNS


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


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_exact_score(value: object) -> str:
    """Return a round-trippable finite score representation for the detail view."""
    number = _num(value)
    return repr(number) if number is not None else "—"


def _format_points(value: float) -> str | None:
    """Keep score units compact without changing their stored precision."""
    points = value * 100
    return f"{points:.12g}" if math.isfinite(points) else None


def format_loss_points(value: object) -> str:
    """Render finite expected loss as unbounded points; never clamp it to a percentage."""
    number = _num(value)
    points = _format_points(number) if number is not None else None
    return f"{points} pts" if points is not None else "—"


def format_benefit_score(value: object) -> str:
    """Render a finite normalized benefit as a score out of 100."""
    number = _num(value)
    if number is None or not 0.0 <= number <= 1.0:
        return "—"
    points = _format_points(number)
    return f"{points}/100" if points is not None else "—"


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
    columns: Sequence[str] = LEDGER_COLUMNS,
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
            else str(assessment.get("assessment_id", "—"))
        ),
        "target": Content(format_target(assessment.get("target_files") or ())),
        "task": Content(format_task(assessment.get("task"))),
        "assessed_commit": Content(short_commit(assessment.get("assessed_commit"))),
        "gate_lane": Content(render_rau_lane(rau, width=LEDGER_LANE_WIDTH)),
        "decision": decision_cell(str(assessment.get("decision", "")), dark=dark),
        "rau": format_rau(rau),
        "expected_loss": format_loss_points(scores.get("expected_loss")),
        "benefit": format_benefit_score(scores.get("benefit")),
        "status": Content(str(assessment.get("terminal_status") or "pending")),
        # M3/M4 reserve these slots before their read-model projections land. Content makes the
        # eventual persisted strings literal rather than Rich markup.
        "prior": Content("—"),
        "lesson": Content("—"),
        "assessed_at": Content(format_assessed_at(assessment.get("assessed_at"))),
    }
    return tuple(cells[column] for column in columns)
