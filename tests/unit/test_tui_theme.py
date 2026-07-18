"""Tests for the Observatory verdict palette (Observatory TUI M3).

The palette is the single source of truth for how each Decision is presented (color + glyph + label).
It must cover every Decision exactly, keep glyphs/labels distinguishable (never depend on color alone),
and stay legible on both a dark and a light terminal. Adding a new Decision without a palette entry must
fail the coverage test.
"""

from __future__ import annotations

from pebra.core.constants import Decision
from pebra.tui.theme import VERDICT_PALETTE, verdict_for


def _srgb_channel(c: int) -> float:
    x = c / 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


_DARK_BG = "#0F1216"
_LIGHT_BG = "#F5F6F8"


def test_palette_covers_every_decision_and_nothing_else() -> None:
    assert set(VERDICT_PALETTE) == {d.value for d in Decision}


def test_every_verdict_has_a_full_capitalized_label() -> None:
    for verdict in VERDICT_PALETTE.values():
        assert verdict.label and verdict.label[0].isupper()


def test_glyphs_and_ascii_fallbacks_are_all_distinct() -> None:
    glyphs = [v.glyph for v in VERDICT_PALETTE.values()]
    ascii_glyphs = [v.ascii_glyph for v in VERDICT_PALETTE.values()]
    assert len(set(glyphs)) == len(glyphs)
    assert len(set(ascii_glyphs)) == len(ascii_glyphs)
    assert all(len(g) == 1 for g in ascii_glyphs)


def test_inspect_first_and_test_first_never_share_a_glyph() -> None:
    inspect, test = VERDICT_PALETTE["inspect_first"], VERDICT_PALETTE["test_first"]
    assert inspect.glyph != test.glyph
    assert inspect.ascii_glyph != test.ascii_glyph


def test_colors_are_legible_on_both_backgrounds() -> None:
    # WCAG contrast >= 3.0 (AA for large text / UI components) on the matching background.
    for verdict in VERDICT_PALETTE.values():
        assert _contrast(verdict.color_dark, _DARK_BG) >= 3.0, verdict.value
        assert _contrast(verdict.color_light, _LIGHT_BG) >= 3.0, verdict.value


def test_verdict_for_known_decision_returns_its_entry() -> None:
    assert verdict_for("proceed") is VERDICT_PALETTE["proceed"]


def test_verdict_for_unknown_decision_returns_safe_fallback() -> None:
    # A decision string the palette doesn't know must never crash the ledger — it degrades to a neutral
    # verdict with a capitalized label and single-char ascii fallback.
    fallback = verdict_for("some_future_decision")
    assert fallback.label and fallback.label[0].isupper()
    assert len(fallback.ascii_glyph) == 1
