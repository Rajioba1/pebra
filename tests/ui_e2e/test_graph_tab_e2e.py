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

    def god_node_map(
        self, repo_root, *, max_files=20, max_symbols_per_file=10, max_nodes=250, max_edges=800
    ):
        return {
            "available": True, "mode": "godmap", "collapsed": False,
            "graph_freshness": "fresh", "fallback_reason": None,
            "nodes": [
                {"id": "file:a.py", "kind": "file_hub", "graph_role": "hub", "shape": "rectangle",
                 "qualified_name": None, "file_path": "a.py", "label": "a.py",
                 "degree": 1, "inbound_count": 1, "symbol_count": 1, "hub_rank": 0},
                {"id": "n:a", "kind": "function", "graph_role": "symbol", "shape": "ellipse",
                 "qualified_name": "a", "file_path": "a.py",
                 "label": "a", "degree": 1, "inbound_count": 0, "outbound_count": 1, "hub_rank": 0,
                 "hub_id": "file:a.py"},
                {"id": "file:b.py", "kind": "file_hub", "graph_role": "hub", "shape": "rectangle",
                 "qualified_name": None, "file_path": "b.py", "label": "b.py",
                 "degree": 2, "inbound_count": 2, "symbol_count": 2, "hub_rank": 1},
                {"id": "n:b", "kind": "class", "graph_role": "symbol", "shape": "ellipse",
                 "qualified_name": "B", "file_path": "b.py",
                 "label": "B", "degree": 2, "inbound_count": 1, "outbound_count": 1, "hub_rank": 1,
                 "hub_id": "file:b.py"},
                {"id": "n:c", "kind": "method", "graph_role": "symbol", "shape": "ellipse",
                 "qualified_name": "B::m", "file_path": "b.py",
                 "label": "m", "degree": 1, "inbound_count": 1, "outbound_count": 0, "hub_rank": 1,
                 "hub_id": "file:b.py"},
            ],
            "edges": [
                {"source": "file:a.py", "target": "n:a", "kind": "contains",
                 "edge_type": "spoke", "line_style": "dashed", "hub_rank": 0},
                {"source": "file:b.py", "target": "n:b", "kind": "contains",
                 "edge_type": "spoke", "line_style": "dashed", "hub_rank": 1},
                {"source": "file:b.py", "target": "n:c", "kind": "contains",
                 "edge_type": "spoke", "line_style": "dashed", "hub_rank": 1},
                {"source": "n:a", "target": "n:b", "kind": "calls",
                 "edge_type": "cross_symbol", "line_style": "solid"},
                {"source": "n:b", "target": "n:c", "kind": "calls",
                 "edge_type": "cross_symbol", "line_style": "solid"},
            ],
            "truncated": False, "total_file_count": 2, "total_symbol_count": 3,
            "total_node_count": 5, "total_edge_count": 5,
        }

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
            scores={
                "edit_confidence": 0.83, "benefit": 0.4, "rau": 0.2,
                "expected_utility": 0.3, "expected_loss": 0.1,
                # Producer shape (assessment_builder): resolved names + percentile live under
                # symbol_fanin; "B" resolves exactly the stub graph's class node -> binds 1 godmap node.
                "symbol_scope_evidence": {
                    "symbol_fan_in_percentile": 0.6,
                    "symbol_fanin": {
                        "resolved_qualified_names": ["B"],
                        "percentile": 0.6,
                    },
                },
            },
            repo_id="r", repo_root="/x",
            model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "t"},
    )
    store.close()
    return db


def _seed_with_lesson(tmp_path) -> str:
    """Seed a VERIFIED learning_context lesson whose target file is the stub graph's b.py node."""
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
            scores={"expected_loss": 0.1, "benefit": 0.82, "rau": 0.31},
            repo_id="r", repo_root="/x", assessed_commit="abc123",
            model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "Fix b", "action_id": "edit-b",
         "revision_envelope": {"expected_files": ["b.py"]}},
    )
    store.persist_guardrails(asm, {"pre_commit_decision": "proceed"})
    store.record_outcome(asm, "completed", {})
    assert store.materialize_learning_context(asm) is not None  # verified lesson exists
    store.close()
    return db


@contextlib.contextmanager
def _serve(db: str, reader=None, *, dev_mode: bool = False):
    import uvicorn

    from pebra.dashboard.server import create_app

    app = create_app(db, "tok", repo_id="r", repo_root="/repo", dev_mode=dev_mode)
    app.state.graph_reader = reader if reader is not None else _StubReader()
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

            # Data flowed end to end: the note reflects the served fixture's god-node map.
            notes = page.eval_on_selector_all(
                "#view-graph .chart-note", "els => els.map(e => e.textContent)"
            )
            assert any("God-node map" in n and "2 file hub(s)" in n and "3 symbol(s)" in n for n in notes), notes

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


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_risk_overlay_binds_assessment_honestly(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)  # seeded assessment resolves "B" -> exactly 1 of the 5 godmap nodes binds
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.add_init_script(
                "window.__csp=[];"
                "document.addEventListener('securitypolicyviolation', e => window.__csp.push("
                "{directive:e.violatedDirective, source:e.sourceFile}));"
            )
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)

            # In the default Structure view the risk-decision legend must be hidden (else a viewer could
            # read decision colours over kind-coloured nodes).
            assert page.locator(".risk-legend").is_hidden()

            # Switch to the Risk view; the caption states the honest aggregate scope + bound count, and
            # the risk-decision legend becomes visible.
            # (Use an exact button name — "Risk" as a substring also matches the "Risk Observatory" header.)
            page.get_by_role("button", name="Risk", exact=True).click()
            page.wait_for_selector(".risk-caption:not([hidden])", timeout=5000)
            assert page.locator(".risk-legend").is_visible()
            cap = page.inner_text(".risk-caption")
            assert "1 of 5" in cap and "assessment-aggregate" in cap and "not per-symbol calibrated" in cap

            # Inspect the bound node (search 'class' -> node B): risk detail shows decision + loss points
            # + the honesty caveat; expected loss is NOT a percentage; the fan-in percentile renders (60%).
            page.fill(".graph-search", "class")
            page.wait_for_selector(".search-row", timeout=5000)
            page.click(".search-row")
            page.wait_for_selector("#graph-inspector .insp-note", timeout=5000)
            insp = page.inner_text("#graph-inspector")
            assert "decision" in insp and "proceed" in insp
            assert "loss pts" in insp
            assert "60%" in insp   # symbol_fan_in_percentile read from the real producer path
            assert "assessment aggregate. This is not per-symbol calibrated risk" in insp

            # Back to Structure view: the risk-decision legend hides again.
            page.get_by_role("button", name="Structure", exact=True).click()
            assert page.locator(".risk-legend").is_hidden()

            # No script/eval or style CSP violation from the risk legend/overlay (swatch colours use
            # CSSOM .style.prop, which style-src does not govern) — only the one known cytoscape <style>.
            violations = page.evaluate("() => window.__csp")
            unexpected = [
                v for v in violations
                if "script" in v["directive"]
                or v["directive"] != "style-src-elem" or "cytoscape" not in (v["source"] or "")
            ]
            assert not unexpected, unexpected
            assert not page_errors, page_errors
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_godmap_live_styles_keep_hubs_rectangular_and_size_symbols_by_fanin(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")

            before = page.evaluate("() => window.__pebraGraph.snapshot()")
            by_id = {n["id"]: n for n in before["nodes"]}
            assert by_id["file:a.py"]["shape"] == "round-rectangle"
            # n:b has inbound=1/outbound=1, n:c has inbound=1/outbound=0. If symbol sizing uses
            # total degree, n:b is wider; if it uses promised inbound fan-in, they match.
            assert by_id["n:b"]["width"] == by_id["n:c"]["width"]

            page.get_by_role("button", name="Risk", exact=True).click()
            page.wait_for_selector(".risk-caption:not([hidden])", timeout=5000)
            after = page.evaluate("() => window.__pebraGraph.snapshot()")
            by_id_after = {n["id"]: n for n in after["nodes"]}
            assert by_id_after["file:a.py"]["shape"] == "round-rectangle"
            assert by_id_after["file:a.py"]["classes"].count("rb-unmatched") == 1
            assert by_id_after["n:b"]["shape"] != "round-rectangle"
            assert any(e["edge_type"] == "spoke" and e["line_style"] == "dashed" for e in after["edges"])
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_learning_overlay_badges_verified_lessons(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed_with_lesson(tmp_path)  # verified lesson on b.py -> stub nodes b/c (file_path b.py)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)

            # The learning legend appears only because a verified lesson exists.
            page.wait_for_selector(".learning-legend:not([hidden])", timeout=5000)

            # A node WITH a matching verified lesson (file b.py) shows the lesson in the inspector,
            # sourced from verified learning_context and labelled "verified lesson" (never "promoted").
            page.fill(".graph-search", "class")  # node B, file b.py
            page.wait_for_selector(".search-row", timeout=5000)
            page.click(".search-row")
            page.wait_for_selector("#graph-inspector .insp-note", timeout=5000)
            insp = page.inner_text("#graph-inspector")
            assert "verified lesson" in insp
            assert "Source: verified learning_context" in insp
            assert "promoted" not in insp.lower()

            # A node WITHOUT a lesson (file a.py) shows no learning section.
            page.fill(".graph-search", "function")  # node a, file a.py
            page.wait_for_selector(".search-row", timeout=5000)
            page.click(".search-row")
            page.wait_for_timeout(200)
            insp_a = page.inner_text("#graph-inspector")
            assert "verified lesson" not in insp_a and "learning_context" not in insp_a
            assert not page_errors, page_errors
            browser.close()


class _PrototypeKeyReader:
    """A graph whose node is literally named 'toString' — an Object.prototype key. It must NOT be
    badged with a lesson just because the lesson lookup map inherits that method."""

    def god_node_map(
        self, repo_root, *, max_files=20, max_symbols_per_file=10, max_nodes=250, max_edges=800
    ):
        return self.full_graph(repo_root)

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        return {
            "available": True, "mode": "symbol", "collapsed": False,
            "graph_freshness": "fresh", "fallback_reason": None,
            "nodes": [
                {"id": "n:ts", "kind": "method", "qualified_name": "toString", "file_path": "x.py",
                 "label": "toString", "degree": 0, "inbound_count": 0, "outbound_count": 0},
            ],
            "edges": [],
            "truncated": False, "total_node_count": 1, "total_edge_count": 0,
        }

    def file_overview(self, repo_root, *, top_n=200):
        return {"available": True, "files": [], "truncated": False, "total_file_count": 0}

    def hot_subgraph(self, *a, **k):
        return {"available": True, "nodes": [], "edges": [], "graph_freshness": "fresh"}


class _CollapsedReader(_StubReader):
    """Serves a file-collapsed graph so the browser can assert the M8 guardrail UX."""

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        return {
            "available": True, "mode": "file", "collapsed": True,
            "graph_freshness": "fresh", "fallback_reason": None,
            "nodes": [
                {"id": "a.py", "kind": "file", "qualified_name": None, "file_path": "a.py",
                 "label": "a.py", "symbol_count": 12},
                {"id": "b.py", "kind": "file", "qualified_name": None, "file_path": "b.py",
                 "label": "b.py", "symbol_count": 10},
            ],
            "edges": [
                {"source": "a.py", "target": "b.py", "kind": "file_aggregate", "weight": 3},
            ],
            "truncated": False, "total_node_count": 50000, "total_file_count": 500,
            "total_edge_count": 120000,
        }


class _SymbolThenCollapsedReader(_CollapsedReader):
    """First render is a symbol graph; subsequent renders are collapsed."""

    def __init__(self) -> None:
        self.calls = 0

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        self.calls += 1
        if self.calls == 1:
            return _StubReader.full_graph(
                self, repo_root, max_nodes=max_nodes, max_edges=max_edges,
                collapse_after=collapse_after,
            )
        return super().full_graph(
            repo_root, max_nodes=max_nodes, max_edges=max_edges, collapse_after=collapse_after,
        )


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_learning_overlay_does_not_prototype_pollute_badge(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed_with_lesson(tmp_path)  # a verified lesson exists (on b.py) — so graphState.learning is set
    with _serve(db, reader=_PrototypeKeyReader()) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            # The 'toString' node matches no lesson (its file x.py and symbol toString aren't lesson
            # keys); it must NOT inherit a badge from Object.prototype.toString.
            page.fill(".graph-search", "toString")
            page.wait_for_selector(".search-row", timeout=5000)
            page.click(".search-row")
            page.wait_for_selector("#graph-inspector .insp-row", timeout=5000)
            insp = page.inner_text("#graph-inspector")
            assert "verified lesson" not in insp and "learning_context" not in insp
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_collapsed_graph_shows_guardrail_notice_and_disables_risk(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, reader=_CollapsedReader(), dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            page.get_by_role("button", name="Full graph (debug)", exact=True).click()
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#view-graph .chart-note'))"
                ".some(e => e.textContent.includes('Collapsed file graph'))",
                timeout=5000,
            )

            notes = page.eval_on_selector_all(
                "#view-graph .chart-note", "els => els.map(e => e.textContent)"
            )
            assert any("Collapsed file graph" in n for n in notes), notes
            assert any("Showing 2 of 500 files" in n for n in notes), notes
            assert any("Risk overlay unavailable in collapsed mode" in n for n in notes), notes

            assert page.locator(".overlay-toggle").count() == 0
            assert page.locator(".risk-legend").is_hidden()
            assert not page_errors, page_errors
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_collapsed_graph_clears_previous_risk_overlay_state(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, reader=_CollapsedReader(), dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)
            page.get_by_role("button", name="Risk", exact=True).click()
            page.wait_for_selector(".risk-caption:not([hidden])", timeout=5000)
            assert page.locator(".risk-legend").is_visible()

            page.get_by_role("button", name="Full graph (debug)", exact=True).click()
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#view-graph .chart-note'))"
                ".some(e => e.textContent.includes('Collapsed file graph'))",
                timeout=5000,
            )

            notes = page.eval_on_selector_all(
                "#view-graph .chart-note", "els => els.map(e => e.textContent)"
            )
            assert any("Risk overlay unavailable in collapsed mode" in n for n in notes), notes
            assert page.locator(".overlay-toggle").count() == 0
            assert page.locator(".risk-legend").is_hidden()
            browser.close()
