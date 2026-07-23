"""Browser smoke test for the dashboard Graph tab (M4).

Behavioural proof (not a source grep): launch the real FastAPI dashboard under uvicorn with a stubbed
graph_reader, drive it with a headless Chromium, and assert that the Graph tab actually renders a
Cytoscape graph (a real <canvas> in the container + the node-count note reflecting the served data),
under the production strict CSP with no eval/console errors.

Runs only when fastapi/jinja2/playwright and a launchable Chromium are available (nox ``ui-e2e``).
"""

from __future__ import annotations

import contextlib
import importlib.util
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("fastapi", "jinja2", "playwright", "uvicorn")),
    reason="requires fastapi/jinja2/playwright/uvicorn (run via nox ui-e2e)",
)


class _StubReader:
    """Serves a tiny fixed structural graph so the browser has real nodes/edges to render."""

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        return {
            "available": True, "mode": "symbol", "collapsed": False,
            "graph_freshness": "fresh", "fallback_reason": None,
            "nodes": [
                {"id": "n:a", "kind": "function", "qualified_name": "a", "file_path": "a.py",
                 "label": "a", "degree": 1, "inbound_count": 0, "outbound_count": 1},
                {"id": "n:b", "kind": "class", "qualified_name": "B", "file_path": "b.py",
                 "label": "B", "degree": 2, "inbound_count": 1, "outbound_count": 1},
                {"id": "n:c", "kind": "method", "qualified_name": "B::m", "file_path": "b.py",
                 "label": "m", "degree": 1, "inbound_count": 1, "outbound_count": 0},
            ],
            "edges": [
                {"source": "n:a", "target": "n:b", "kind": "calls"},
                {"source": "n:b", "target": "n:c", "kind": "calls"},
            ],
            "truncated": False, "total_node_count": 3, "total_edge_count": 2,
        }

    def file_overview(self, repo_root, *, top_n=200):
        return {"available": True, "files": [{"file_path": "b.py", "distinct_caller_count": 2}],
                "truncated": False, "total_file_count": 1}

    def hot_subgraph(self, *a, **k):
        return {"available": True, "nodes": [], "edges": [], "graph_freshness": "fresh"}


def _seed(tmp_path) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
            scores={"edit_confidence": 0.83}, repo_id="r", repo_root="/x",
            model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "t"},
    )
    store.close()
    return db


@contextlib.contextmanager
def _serve(db: str):
    import uvicorn

    from pebra.dashboard.server import create_app

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_tab_renders_cytoscape_nodes_under_csp(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            # Record every CSP violation with its directive/source so we can assert exactly which
            # (if any) are tolerated, rather than swallowing all style-src noise blindly.
            page.add_init_script(
                "window.__csp=[];"
                "document.addEventListener('securitypolicyviolation', e => window.__csp.push("
                "{directive:e.violatedDirective, blockedURI:e.blockedURI, source:e.sourceFile}));"
            )
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))

            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")

            # Cytoscape actually initialised and drew into the container (a real <canvas> child) AND the
            # canvas is sized to the container — i.e. the graph rendered, not a zero-size stub.
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            render = page.evaluate(
                "() => { const c = document.querySelector('#graph-cy');"
                " const cv = c.querySelector('canvas');"
                " return { pos: getComputedStyle(c).position, cw: c.clientWidth,"
                " canvasW: cv ? cv.width : 0, canvasCount: c.querySelectorAll('canvas').length }; }"
            )
            assert render["canvasCount"] >= 1
            assert render["canvasW"] == render["cw"] and render["cw"] > 0  # canvas fills the container
            assert render["pos"] == "relative"  # our style.css positions it (blocked injection is moot)

            # Data flowed end to end: the note reflects the served fixture's 3 nodes / 2 edges.
            notes = page.eval_on_selector_all(
                "#view-graph .chart-note", "els => els.map(e => e.textContent)"
            )
            assert any("3 node(s)" in n and "2 edge(s)" in n for n in notes), notes

            # No page/script errors at all.
            assert not page_errors, page_errors
            # Strict CSP held: the ONLY tolerated violation is Cytoscape's injected container-position
            # <style> (style-src-elem, neutralised by our own .graph-cy CSS). NO script-src/eval
            # violation may occur — that would mean the vendored bundle tried to execute inline/eval.
            violations = page.evaluate("() => window.__csp")
            script_violations = [v for v in violations if "script" in v["directive"]]
            assert not script_violations, script_violations
            unexpected = [
                v for v in violations
                if v["directive"] != "style-src-elem" or "cytoscape" not in (v["source"] or "")
            ]
            assert not unexpected, unexpected
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_instance_is_destroyed_when_navigating_away(tmp_path) -> None:
    # The WebGL Cytoscape instance must not linger off-tab (leaked GL context / RAF loop). Leaving the
    # Graph tab destroys it (its canvases are removed); returning re-renders it.
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            assert page.eval_on_selector_all("#graph-cy canvas", "els => els.length") >= 1

            # Navigate away: the graph instance is torn down (container emptied of canvases).
            page.evaluate("() => { location.hash = '#overview'; }")
            page.wait_for_function(
                "() => { const c = document.querySelector('#graph-cy');"
                " return !c || c.querySelectorAll('canvas').length === 0; }",
                timeout=5000,
            )

            # Return to the Graph tab: it re-renders a fresh instance.
            page.evaluate("() => { location.hash = '#graph'; }")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            assert page.eval_on_selector_all("#graph-cy canvas", "els => els.length") >= 1
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_search_inspector_and_layout_controls(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)

            # Search "class" matches exactly the one class node (kind=class) and lists it.
            page.fill(".graph-search", "class")
            page.wait_for_selector(".search-row", timeout=5000)
            rows = page.eval_on_selector_all(".search-row", "els => els.map(e => e.textContent)")
            assert rows and any("B" in r for r in rows), rows

            # Inspector is the keyboard-reachable a11y fallback.
            assert page.get_attribute("#graph-inspector", "tabindex") == "0"

            # Activating a result populates the inspector with that node's real fields.
            page.click(".search-row")
            page.wait_for_selector("#graph-inspector .insp-row", timeout=5000)
            insp = page.inner_text("#graph-inspector")
            assert "kind" in insp and "class" in insp and "fan-in" in insp

            # Layout buttons operate on the live instance without throwing.
            page.click("text=Circle")
            page.wait_for_timeout(300)
            page.click("text=Grid")
            page.wait_for_timeout(300)
            assert not page_errors, page_errors
            browser.close()
