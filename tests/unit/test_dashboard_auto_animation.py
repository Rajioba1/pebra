"""Milestone 1: the explicit Auto button animates the layout once per click (reduced-motion aware),
while the initial/live-refresh path stays silent. Deterministic source guards over app.js; the visible
motion itself is proven in the ui-e2e lane."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _fn_body(js: str, name: str) -> str:
    body = re.search(r"function " + name + r"\([^)]*\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert body, f"{name}() body not found"
    return body.group(1)


def test_auto_click_animates_once_reduced_motion_aware() -> None:
    body = _fn_body(_js(), "runGraphLayout")
    # The "auto" intent must be captured BEFORE it is resolved to a concrete layout name.
    assert "const isAutoClick = " in body, "must capture the auto-click intent"
    assert body.index("isAutoClick") < body.index("layoutNameFor("), "capture auto before resolving it"
    # The cose branch animates only on an explicit auto click, and only when motion is allowed.
    assert "coseOptions(cy.nodes().length, false, isAutoClick && !reduceMotion)" in body, (
        "auto click must request an animated cose layout, gated on reduced-motion"
    )


def test_cose_options_supports_bounded_animation() -> None:
    body = _fn_body(_js(), "coseOptions")
    assert "animate" in re.search(r"function coseOptions\(([^)]*)\)", _js()).group(1), (
        "coseOptions needs an animate parameter"
    )
    assert re.search(r"animationDuration:\s*animate\s*\?\s*600", body), "animated reveal is a bounded ~600ms"


def test_layoutstop_resyncs_zoom_disclosed_labels() -> None:
    body = _fn_body(_js(), "runGraphLayout")
    stop = re.search(r'layout\.one\("layoutstop".*?\}\);', body, re.DOTALL)
    assert stop, "layoutstop handler not found"
    assert "updateSymbolLabelVisibility()" in stop.group(0), (
        "an animated fit must re-sync the zoom-gated symbol labels"
    )


def test_initial_layout_path_stays_silent() -> None:
    js = _js()
    # layoutFor (initial construction + live refresh) must NOT pass the animate arg — no reveal on the tick.
    assert "coseOptions(nodeCount, true)" in js, "initial layout must call coseOptions without animation"
    assert "coseOptions(nodeCount, true, true)" not in js, "initial/live path must never animate"
