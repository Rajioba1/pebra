"""Tests for the PEBRA wordmark banner (pure content)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from pebra.tui.widgets.banner import (  # noqa: E402
    _GATE_INDEX,
    _LANE_LEN,
    _REST_INDEX,
    banner_content,
    lane,
)
from pebra.tui.theme import VERDICT_PALETTE  # noqa: E402


def test_lane_has_fixed_length_gate_and_never_uses_a_decision_glyph() -> None:
    rendered = lane(_REST_INDEX)
    assert len(rendered) == _LANE_LEN
    assert rendered[_GATE_INDEX] == "│"
    assert not {verdict.glyph for verdict in VERDICT_PALETTE.values()} & set(rendered)


def test_lane_draws_no_marker_for_negative_index_but_keeps_the_gate() -> None:
    rendered = lane(-1)
    assert "▸" not in rendered
    assert rendered[_GATE_INDEX] == "│"


def test_banner_content_carries_wordmark_and_tagline() -> None:
    content = banner_content(_REST_INDEX)
    assert "P E B R A" in content.plain
    assert "pre-edit benefit / risk" in content.plain
    assert not {verdict.glyph for verdict in VERDICT_PALETTE.values()} & set(content.plain)
