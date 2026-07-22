"""Tests for the RAU lane (Observatory TUI M3).

The RAU lane is a fixed [-1.0, +1.0] mini-track with the gate (RAU=0, engine Gate 4) at center. It encodes
RAU ONLY — the decision is a separate glyph/label. The visual window is fixed: only the marker clamps,
overflow is shown with an arrow, and the numeric RAU value (rendered separately) is never clamped.
"""

from __future__ import annotations

from textual.content import Content

from pebra.core.constants import ActionStatus
from pebra.tui.theme import VERDICT_PALETTE
from pebra.tui.widgets.ledger_table import (
    LEDGER_COLUMN_WIDTHS,
    LEDGER_COLUMNS,
    format_assessed_at,
    format_rau,
    format_target,
    format_task,
    ledger_row,
    render_rau_lane,
)

_DECISION_GLYPHS = {v.glyph for v in VERDICT_PALETTE.values()}
_MARKER = "●"
_GATE = "│"
_WIDTH = 13


def test_lane_width_is_fixed_for_every_input() -> None:
    for rau in (-1.5, -0.2, 0.0, 0.2, 1.5, None, float("nan"), float("inf"), float("-inf")):
        assert len(render_rau_lane(rau, width=_WIDTH)) == _WIDTH


def test_marker_is_never_a_decision_glyph() -> None:
    for rau in (-0.2, 0.0, 0.2):
        lane = render_rau_lane(rau, width=_WIDTH)
        assert _MARKER in lane
        assert not (_DECISION_GLYPHS & set(lane)), lane


def test_in_range_marker_sits_correctly_relative_to_the_gate() -> None:
    center = _WIDTH // 2
    assert render_rau_lane(-0.2, width=_WIDTH).index(_MARKER) < center
    assert render_rau_lane(0.2, width=_WIDTH).index(_MARKER) > center
    assert render_rau_lane(0.0, width=_WIDTH).index(_MARKER) == center


def test_small_nonzero_rau_never_hides_its_sign_on_the_gate() -> None:
    center = _WIDTH // 2
    # tiny values that would otherwise round onto the gate cell must still show their side
    assert render_rau_lane(0.03, width=_WIDTH).index(_MARKER) > center
    assert render_rau_lane(-0.03, width=_WIDTH).index(_MARKER) < center
    assert render_rau_lane(0.0, width=_WIDTH).index(_MARKER) == center  # only exact zero on the gate


def test_overflow_shows_an_arrow_not_the_marker() -> None:
    low = render_rau_lane(-1.5, width=_WIDTH)
    high = render_rau_lane(1.5, width=_WIDTH)
    assert "«" in low and _MARKER not in low
    assert "»" in high and _MARKER not in high


def test_infinity_is_treated_as_overflow() -> None:
    assert "»" in render_rau_lane(float("inf"), width=_WIDTH)
    assert "«" in render_rau_lane(float("-inf"), width=_WIDTH)


def test_missing_and_nan_render_no_marker_but_keep_the_gate() -> None:
    for rau in (None, float("nan")):
        lane = render_rau_lane(rau, width=_WIDTH)
        assert _MARKER not in lane
        assert _GATE in lane


def test_format_rau_is_signed_and_authoritative() -> None:
    assert format_rau(0.14) == "+0.14"
    assert format_rau(-0.31) == "-0.31"
    assert format_rau(0.0) == "+0.00"


def test_format_rau_missing_and_nonfinite_render_dash() -> None:
    assert format_rau(None) == "—"
    assert format_rau(float("nan")) == "—"
    assert format_rau(float("inf")) == "—"


def test_single_target_uses_compact_filename() -> None:
    assert format_target(["src/auth/login.py"]) == "login.py"


def test_multiple_targets_render_filename_plus_count() -> None:
    assert format_target(["src/auth.py", "src/session.py", "tests/test_auth.py"]) == "auth.py +2"


def test_unavailable_target_is_explicit() -> None:
    assert format_target([]) == "target unavailable"


def test_assessed_at_is_compact_and_legacy_safe() -> None:
    assert format_assessed_at("2026-07-20T12:34:56.123456+00:00") == "2026-07-20 12:34"
    assert format_assessed_at(None) == "—"


def test_task_display_is_bounded_without_mutating_row_data() -> None:
    row = {"task": "  Update   authentication validation across every entry point  "}

    assert format_task(row["task"]) == "Update authentication valid…"
    assert row["task"] == "  Update   authentication validation across every entry point  "


def test_complete_ledger_reserves_literal_cells_for_persisted_text() -> None:
    cells = ledger_row(
        {
            "assessment_id": "asm_[one]",
            "target_files": ["src/[target].py"],
            "task": "update Dict[str, Any]",
            "assessed_commit": "[bold]abcdef0",
            "terminal_status": "[pending]",
            "assessed_at": "[bold]time",
            "scores": {"rau": 0.1, "expected_loss": 0.2, "benefit": 0.3},
        }
    )
    by_column = dict(zip(LEDGER_COLUMNS, cells, strict=True))

    for name in ("target", "task", "assessed_commit", "status", "prior", "lesson", "assessed_at"):
        assert isinstance(by_column[name], Content)
    assert by_column["assessed_commit"].plain == "[bold]a"
    assert by_column["status"].plain == "[pending]"
    assert by_column["assessed_at"].plain == "[bold]time"


def test_status_column_fits_every_terminal_status_without_clipping() -> None:
    supported = {status.value for status in ActionStatus} | {"pending"}

    assert LEDGER_COLUMN_WIDTHS["status"] >= max(map(len, supported))
    for status in ("pending", "completed"):
        cells = ledger_row({"terminal_status": status, "scores": {}})
        cell = dict(zip(LEDGER_COLUMNS, cells, strict=True))["status"]
        assert isinstance(cell, Content)
        assert cell.plain == status


# --- Milestone 2 score-unit contract ----------------------------------------------------------
# The ledger renders expected loss as unbounded points and normalized benefit as a score out of 100.

import math  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0 pts"),
        (0.1, "10 pts"),
        (0.82, "82 pts"),
        (1.45, "145 pts"),  # unbounded: expected_loss is Sigma(p*disutility), never clamped to 100
    ],
)
def test_loss_points_render_unbounded_and_never_clamped(value: float, expected: str) -> None:
    from pebra.tui.widgets.ledger_table import format_loss_points

    assert format_loss_points(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, True, 10**1000, 1e308, float("nan"), float("inf"), float("-inf")],
)
def test_loss_points_reject_missing_boolean_and_nonfinite(value: object) -> None:
    from pebra.tui.widgets.ledger_table import format_loss_points

    assert format_loss_points(value) == "—"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, "0/100"), (0.1, "10/100"), (0.82, "82/100"), (1.0, "100/100")],
)
def test_benefit_score_renders_as_n_over_100(value: float, expected: str) -> None:
    from pebra.tui.widgets.ledger_table import format_benefit_score

    assert format_benefit_score(value) == expected


@pytest.mark.parametrize(
    "value", [None, True, 10**1000, -0.01, 1.01, float("nan"), float("inf")]
)
def test_benefit_score_rejects_missing_boolean_and_nonfinite(value: object) -> None:
    from pebra.tui.widgets.ledger_table import format_benefit_score

    assert format_benefit_score(value) == "—"


def test_ledger_row_uses_honest_loss_and_benefit_units() -> None:
    cells = ledger_row(
        {
            "assessment_id": "asm_1",
            "scores": {"expected_loss": 1.45, "benefit": 0.82},
        }
    )
    by_column = dict(zip(LEDGER_COLUMNS, cells, strict=True))
    assert by_column["expected_loss"] == "145 pts"
    assert by_column["benefit"] == "82/100"


def test_loss_column_uses_content_width_instead_of_clipping_to_header() -> None:
    assert "expected_loss" not in LEDGER_COLUMN_WIDTHS


def test_expected_loss_is_genuinely_unbounded_in_scoring_math() -> None:
    """Characterization: the domain assumption behind '145 pts' — expected_loss can exceed 1.0.

    Locks the fact (not just the display) so a future clamp anywhere upstream is caught here."""
    from pebra.core.score_math import expected_loss

    events = [
        {"p_event": 1.0, "disutility": 0.8},
        {"p_event": 1.0, "disutility": 0.65},
    ]
    total, _components = expected_loss(events)  # returns (total, per-event components)
    assert total > 1.0 and math.isfinite(total)
    assert round(total, 2) == 1.45  # the exact '145 pts' regression case
