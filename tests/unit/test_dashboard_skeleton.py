"""Milestone 3: skeleton placeholders show only on a tab's FIRST data load — never on the 1.5s live
refresh, and never stacked on a tab that already painted a shell. Source guards over app.js/style.css."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"
_STYLE = ROOT / "pebra" / "dashboard" / "static" / "style.css"


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _route_body(js: str) -> str:
    body = re.search(r"async function route\(\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert body, "route() body not found"
    return body.group(1)


def test_skeleton_gated_on_first_tab_load_not_the_per_call_attr() -> None:
    js = _js()
    assert "const tabEverLoaded = new Set()" in js, "need a persistent first-load set, distinct per tab"
    body = _route_body(js)
    assert "!tabEverLoaded.has(" in body, "skeleton must gate on first-ever load of the tab"
    assert "tabEverLoaded.add(" in body, "a tab must be marked loaded after a successful render"
    # The per-call data-loaded attribute is reset every route() (incl. live ticks) — it must NOT be the gate.
    assert 'removeAttribute("data-loaded")' in body, "existing data-loaded reset must remain"
    assert "firstLoad" in body, "first-load decision must be explicit"


def test_skeleton_is_deferred_and_guards_a_nonempty_view() -> None:
    body = _route_body(_js())
    timer = re.search(r"setTimeout\(function \(\)\s*\{(.*?)\}, 150\)", body, re.DOTALL)
    assert timer, "skeleton must be a ~150ms deferred timer (fast loads never flash it)"
    assert "hasChildNodes()" in timer.group(1), "skeleton must no-op if the view already painted a shell"
    assert "renderSkeleton(" in timer.group(1), "the timer renders the skeleton"
    assert "clearTimeout(" in body, "the timer must be cancelled once the render resolves"


def test_skeleton_styles_exist_and_ride_the_global_reduced_motion_switch() -> None:
    css = _STYLE.read_text(encoding="utf-8")
    assert ".skeleton-block" in css, "skeleton block style missing"
    assert "@keyframes skeleton-shimmer" in css, "skeleton shimmer keyframes missing"
    # No dedicated reduced-motion branch needed — the global kill-switch already covers it.
    assert "* { animation: none !important; }" in css, "global reduced-motion kill-switch must remain"
