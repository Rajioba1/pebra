"""Observatory TUI theme tokens — the single source of truth for the surface's colors and for how each
Decision is presented.

Two parts:
  * the base "instrument" chrome tokens (deep slate-ink), exposed as Textual CSS variables; and
  * VERDICT_PALETTE — one entry per Decision, pairing a color with a glyph AND a full text label so the
    encoding never depends on color alone (colorblind-safe by construction). DataTable cells render the
    glyph/label as Textual Content using these hexes directly; TCSS chrome uses css_variables().

Token names are namespaced (``observatory-*``) so merging them into App.get_css_variables() never clobbers
Textual's built-in design-system variables.
"""

from __future__ import annotations

from dataclasses import dataclass

_OBSERVATORY_TOKENS = {
    "observatory-ink": "#0F1216",      # deep slate-ink background — an instrument, not a void
    "observatory-surface": "#161A20",
    "observatory-panel": "#1B2028",
    "observatory-hairline": "#2A2F37",  # box-drawing rules
    "observatory-text": "#C6CBD2",
    "observatory-muted": "#6B727C",     # labels in a quieter register
}


def css_variables() -> dict[str, str]:
    """Observatory theme tokens as Textual CSS variables (name -> value), merged into the app's
    ``get_css_variables()`` alongside Textual's built-ins."""
    return dict(_OBSERVATORY_TOKENS)


@dataclass(frozen=True)
class Verdict:
    """How one Decision is presented: color (per background) + glyph + ASCII fallback + full label."""

    value: str
    label: str
    glyph: str
    ascii_glyph: str
    color_dark: str   # legible on the deep slate-ink dark background
    color_light: str  # legible on a light terminal


# One diverging semantic scale from the safe end (proceed, green) to the blocked end (reject, clay-red),
# each backed by a distinct glyph. inspect_first and test_first deliberately use different glyphs — the
# eye must tell them apart without reading color. Hexes clear WCAG contrast >= 3.0 on their background.
VERDICT_PALETTE: dict[str, Verdict] = {
    "proceed": Verdict("proceed", "Proceed", "▸", ">", "#3FB950", "#1A7F37"),
    "inspect_first": Verdict("inspect_first", "Inspect first", "◇", "i", "#6FB0A0", "#2C7A6B"),
    "test_first": Verdict("test_first", "Test first", "◎", "t", "#B8B84A", "#6E7A1E"),
    "revise_safer": Verdict("revise_safer", "Revise safer", "↩", "~", "#E3B341", "#9A6D00"),
    "ask_human": Verdict("ask_human", "Ask human", "‖", "?", "#E8873B", "#B5560F"),
    "reject": Verdict("reject", "Reject", "✕", "x", "#F85149", "#C4271C"),
}

_UNKNOWN_VERDICT = Verdict("unknown", "Unknown", "·", ".", "#8B929C", "#5A616B")


def verdict_for(decision: str) -> Verdict:
    """Return the Verdict for a decision string. An unrecognized decision degrades to a neutral
    'Unknown' verdict rather than crashing the ledger (the coverage test guards known decisions)."""
    return VERDICT_PALETTE.get(decision, _UNKNOWN_VERDICT)
