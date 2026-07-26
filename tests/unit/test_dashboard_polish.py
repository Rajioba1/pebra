"""Milestone 4 polish: restrained hero elevation, a 4/8 spacing grid, and directional empty states.
Deterministic guards over the served style.css / app.js."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_STYLE = ROOT / "pebra" / "dashboard" / "static" / "style.css"
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"


def _css() -> str:
    return _STYLE.read_text(encoding="utf-8")


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    block = re.search(
        r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert block, f"selector {selector!r} not found"
    return block.group(1)


def test_hero_elevation_without_shadow() -> None:
    css = _css()
    body = _rule(css, ".card.hero, .stat-row .card")
    assert "var(--surface-2)" in body, "hero/KPI cards must step up to --surface-2"
    assert "box-shadow" not in body, "no shadow on data surfaces"
    assert 'graphCard.classList.add("hero")' in _js(), "graph card must get the hero emphasis"


def test_spacing_is_on_the_4_8_grid() -> None:
    css = _css()
    assert "14px 22px" not in css and "9px 10px" not in css and "18px 22px 28px" not in css, (
        "off-grid spacing values remain"
    )
    assert "padding: 16px 24px;" in _rule(css, ".topbar"), "topbar padding off 4/8 grid"
    assert "padding: 8px 10px;" in _rule(css, "tbody td"), "table cell padding off 4/8 grid"
    assert "padding: 16px 24px 28px;" in _rule(css, "main"), "main padding off 4/8 grid"


def test_empty_states_give_direction() -> None:
    js = _js()
    # Pinned substring must survive (asserted elsewhere too) — reword by appending, never rewriting it.
    assert "No verified completed outcomes have produced recallable lessons yet." in js
    # Unpinned empties now point at the next action.
    assert "Run pebra assess" in js, "the activity empty state should tell the user how to populate it"
    assert "Run pebra verify" in js, "the measured-benefit empty state should point at verify"
    assert "learning loop promotes a snapshot" in js, "the learned-rules empty state should explain when it fills"
