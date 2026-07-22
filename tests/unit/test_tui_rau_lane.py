"""Tests for the RAU lane (Observatory TUI M3).

The RAU lane is a fixed [-1.0, +1.0] mini-track with the gate (RAU=0, engine Gate 4) at center. It encodes
RAU ONLY — the decision is a separate glyph/label. The visual window is fixed: only the marker clamps,
overflow is shown with an arrow, and the numeric RAU value (rendered separately) is never clamped.
"""

from __future__ import annotations

from pebra.tui.theme import VERDICT_PALETTE
from pebra.tui.widgets.ledger_table import (
    format_assessed_at,
    format_rau,
    format_target,
    format_task,
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


# --- Milestone 0 characterization lock: forward-looking score-unit formatters ---------------
# `format_loss_points` / `format_benefit_score` are introduced in Milestone 2. These xfail(strict)
# cases are the executable spec for that milestone: when M2 lands them, the tests XPASS and strict
# xfail fails, forcing removal of the marker. Imports live inside each test so the not-yet-existing
# symbol raises at call time (an expected failure), never a collection error for this whole file.

import math  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.xfail(strict=True, reason="Milestone 2: format_loss_points not implemented yet")
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


@pytest.mark.xfail(strict=True, reason="Milestone 2: format_loss_points not implemented yet")
@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), float("-inf")])
def test_loss_points_reject_missing_boolean_and_nonfinite(value: object) -> None:
    from pebra.tui.widgets.ledger_table import format_loss_points

    assert format_loss_points(value) == "—"


@pytest.mark.xfail(strict=True, reason="Milestone 2: format_benefit_score not implemented yet")
@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, "0/100"), (0.1, "10/100"), (0.82, "82/100"), (1.0, "100/100")],
)
def test_benefit_score_renders_as_n_over_100(value: float, expected: str) -> None:
    from pebra.tui.widgets.ledger_table import format_benefit_score

    assert format_benefit_score(value) == expected


@pytest.mark.xfail(strict=True, reason="Milestone 2: format_benefit_score not implemented yet")
@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_benefit_score_rejects_missing_boolean_and_nonfinite(value: object) -> None:
    from pebra.tui.widgets.ledger_table import format_benefit_score

    assert format_benefit_score(value) == "—"


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
