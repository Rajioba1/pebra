"""Graph lifecycle fixes: P1 — route()'s error path must not orphan a live Cytoscape/WebGL instance;
P0 — a live-refresh tick must not remount the Graph tab (which wipes camera/selection and races the
controls). Deterministic source guards over app.js; the runtime camera-survival + retry behaviours are
proven in the ui-e2e lane."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _fn_body(js: str, name: str) -> str:
    body = re.search(r"(?:async )?function " + name + r"\([^)]*\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert body, f"{name}() body not found"
    return body.group(1)


def test_route_catch_destroys_cytoscape_before_clearing_view() -> None:
    # P1: a throw after renderCy created the instance must tear it down before the container is cleared,
    # or graphState.cy is left non-null but detached (a zombie that makes the graph controls no-op).
    route = _fn_body(_js(), "route")
    catch = re.search(r"catch \(e\) \{(.*?)\} finally", route, re.DOTALL)
    assert catch, "route() try/catch/finally not found"
    body = catch.group(1)
    assert "destroyCy()" in body, "route() catch must destroy a live cy before clearing the view"
    assert body.index("destroyCy()") < body.index("clear(view)"), "destroy must precede clear"


def test_live_refresh_does_not_remount_the_graph() -> None:
    # P0: on a live tick the Graph tab must refresh only the drifting overlays (assessment list, lessons),
    # and conditionally apply changed graph data, never route()->renderGraph (which replaces controls).
    body = _fn_body(_js(), "refreshLiveView")
    assert 'tab === "graph"' in body and "graphState.cy" in body, "need a graph fast-path guard"
    assert body.index('tab === "graph"') < body.index("await route()"), "graph branch must precede route()"
    # The fast path polls structure and refreshes overlays in place, then returns before route().
    assert "refreshGraphStructure(" in body, "incrementally synced graph changes must be observed"
    assert "refreshRiskOverlay(" in body, "risk controls must refresh without DOM replacement"
    assert "setupRiskOverlay(" not in body, "live ticks must not rebuild risk controls"
    assert "loadLearningOverlay(" in body, "learning badges must refresh in place"
    assert body.index("loadLearningOverlay(") < body.index("await route()"), (
        "overlay refresh must be in the early-return branch, not the full-remount path"
    )


def test_hotspots_retries_after_a_failed_expand() -> None:
    # P2: the loaded flag must reflect the load RESULT, not be set eagerly — else a failed expand can
    # never retry on re-open.
    js = _js()
    handler = re.search(r'hotspots\.addEventListener\("toggle",.*?\}\);', js, re.DOTALL)
    assert handler, "hotspots toggle handler not found"
    hb = handler.group(0)
    assert "hotspotsLoaded = true" not in hb, "must not mark loaded before the load resolves"
    assert "hotspotsLoaded = await loadHotspotsTable(" in hb, "flag must reflect the load result"
    assert "hotspotsLoading" in hb, "an in-flight load must suppress duplicate reopen requests"
    body = _fn_body(js, "loadHotspotsTable")
    assert body.count("return false;") >= 2, "unavailable + error must report a retryable failure"
    assert "return true;" in body, "a successful load must report success (load once)"
