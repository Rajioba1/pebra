"""Observatory TUI theme tokens — the single source of truth for the surface's colors.

M2 defines only the base "instrument" tokens (deep slate-ink chrome). The full per-Decision verdict
palette + glyphs (the RAU-lane color encoding) lands in M3, added to this same module so Rich-Text cells
and TCSS share one dict. Token names are namespaced (``observatory-*``) so merging them into
``App.get_css_variables()`` never clobbers Textual's built-in design-system variables.
"""

from __future__ import annotations

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
