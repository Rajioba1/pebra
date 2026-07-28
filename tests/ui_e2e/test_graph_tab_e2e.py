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


class _UnavailableReader(_StubReader):
    def god_node_map(
        self, repo_root, *, max_files=20, max_symbols_per_file=10, max_nodes=250, max_edges=800
    ):
        return {
            "available": False,
            "mode": "godmap",
            "nodes": [],
            "edges": [],
            "fallback_reason": "codegraph graph data unavailable",
            "setup_command": "pebra setup-graph --fix --repo-root .",
            "setup_hint": "Initialize or repair the local CodeGraph index, then refresh this tab.",
        }


class _RetryOverviewReader(_StubReader):
    """First hotspot read is unavailable; the next succeeds."""

    def __init__(self) -> None:
        self.overview_calls = 0

    def file_overview(self, repo_root, *, top_n=200):
        self.overview_calls += 1
        if self.overview_calls == 1:
            return {
                "available": False,
                "files": [],
                "truncated": False,
                "total_file_count": 0,
                "fallback_reason": "temporary graph read failure",
            }
        return super().file_overview(repo_root, top_n=top_n)


class _BlockingOverviewReader(_StubReader):
    """Hold the first hotspot read open so close/reopen can exercise the single-flight guard."""

    def __init__(self) -> None:
        self.overview_calls = 0
        self.overview_started = threading.Event()
        self.release_overview = threading.Event()

    def file_overview(self, repo_root, *, top_n=200):
        self.overview_calls += 1
        self.overview_started.set()
        self.release_overview.wait(timeout=5)
        return super().file_overview(repo_root, top_n=top_n)


class _ChangingGraphReader(_StubReader):
    """Add one node after the initial graph read, as an external assess/sync can do."""

    def __init__(self) -> None:
        self.godmap_calls = 0

    def god_node_map(
        self, repo_root, *, max_files=20, max_symbols_per_file=10, max_nodes=250, max_edges=800
    ):
        self.godmap_calls += 1
        graph = super().god_node_map(
            repo_root,
            max_files=max_files,
            max_symbols_per_file=max_symbols_per_file,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if self.godmap_calls >= 2:
            graph["nodes"].append(
                {
                    "id": "n:new",
                    "kind": "function",
                    "graph_role": "symbol",
                    "shape": "ellipse",
                    "qualified_name": "new",
                    "file_path": "b.py",
                    "label": "new",
                    "degree": 1,
                    "inbound_count": 1,
                    "outbound_count": 0,
                    "hub_rank": 1,
                    "hub_id": "file:b.py",
                }
            )
            graph["edges"].append(
                {
                    "source": "file:b.py",
                    "target": "n:new",
                    "kind": "contains",
                    "edge_type": "spoke",
                    "line_style": "dashed",
                    "hub_rank": 1,
                }
            )
            graph["total_symbol_count"] = 4
            graph["total_node_count"] = 6
            graph["total_edge_count"] = 6
        return graph


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
def test_graph_is_default_and_hotspots_load_lazily(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            overview_requests: list[str] = []
            page.on(
                "request",
                lambda request: overview_requests.append(request.url)
                if "/graph/overview" in request.url
                else None,
            )

            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok", wait_until="networkidle")
            page.wait_for_selector("#graph-cy canvas", timeout=10000)

            graph_tab = page.get_by_role("tab", name="Graph", exact=True)
            assert graph_tab.get_attribute("aria-selected") == "true"
            assert page.locator('[data-testid="graph"]').is_visible()
            details = page.locator('[data-testid="repo-hotspots"]')
            assert details.get_attribute("open") is None
            assert overview_requests == []

            details.locator("summary").click()
            page.wait_for_selector('[data-testid="repo-hotspots"] tbody')
            assert len(overview_requests) == 1
            assert page.get_by_role("button", name="b.py", exact=True).is_visible()
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_hotspots_retries_after_unavailable_read(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    reader = _RetryOverviewReader()
    with _serve(db, reader=reader) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#graph",
                    wait_until="networkidle",
                )
                details = page.locator('[data-testid="repo-hotspots"]')
                summary = details.locator("summary")

                summary.click()
                page.wait_for_selector('[data-testid="repo-hotspots"] .empty.warn')
                summary.click()
                summary.click()
                page.wait_for_selector('[data-testid="repo-hotspots"] tbody')

                assert reader.overview_calls == 2
                assert page.get_by_role("button", name="b.py", exact=True).is_visible()
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_hotspots_close_reopen_keeps_one_request_in_flight(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    reader = _BlockingOverviewReader()
    with _serve(db, reader=reader) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#graph",
                    wait_until="networkidle",
                )
                details = page.locator('[data-testid="repo-hotspots"]')
                summary = details.locator("summary")

                summary.click()
                assert reader.overview_started.wait(timeout=2)
                summary.click()
                summary.click()
                time.sleep(0.15)
                calls_while_blocked = reader.overview_calls
                reader.release_overview.set()
                page.wait_for_selector('[data-testid="repo-hotspots"] tbody')

                assert calls_while_blocked == 1
                assert reader.overview_calls == 1
            finally:
                reader.release_overview.set()
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_unavailable_shows_setup_without_blank_stage(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, reader=_UnavailableReader()) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok", wait_until="networkidle")

            assert page.locator("#view-graph .empty.warn").is_visible()
            assert page.locator("#graph-cy").is_hidden()
            assert page.locator("#view-graph .controls").is_hidden()
            assert "pebra setup-graph --fix" in page.locator("#view-graph .empty.warn").inner_text()
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_route_error_destroys_the_detached_cytoscape_instance(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.route(
                    "**/api/repos/r/learning/context*",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"status":"available","items":"malformed"}',
                    ),
                )
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#graph",
                    wait_until="networkidle",
                )
                page.wait_for_selector("#view-graph .empty")

                assert "Error loading graph" in page.locator("#view-graph .empty").inner_text()
                assert page.locator("#graph-cy canvas").count() == 0
                assert page.evaluate("() => window.__pebraGraph.snapshot().nodes.length") == 0
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_dashboard_tabs_support_keyboard_navigation_and_legacy_hashes(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#overview", wait_until="networkidle")

            activity = page.get_by_role("tab", name="Activity", exact=True)
            assert activity.get_attribute("aria-selected") == "true"
            assert activity.get_attribute("tabindex") == "0"
            activity.focus()
            activity.press("ArrowLeft")
            page.wait_for_url("**#graph")
            graph = page.get_by_role("tab", name="Graph", exact=True)
            page.wait_for_function(
                "() => document.querySelector('[data-tab=\"graph\"]')"
                ".getAttribute('aria-selected') === 'true'"
            )
            assert graph.get_attribute("aria-selected") == "true"
            assert graph.evaluate("node => document.activeElement === node") is True
            tabindexes = page.locator('[role="tab"]').evaluate_all(
                "tabs => tabs.map(tab => [tab.textContent.trim(), tab.getAttribute('tabindex')])"
            )
            assert tabindexes == [["Graph", "0"], ["Activity", "-1"], ["Learning", "-1"]]
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

            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(e.stack or str(e)))
            page.get_by_role("button", name="Auto", exact=True).click()
            # Navigate away during the bounded animation: the graph instance is torn down (container
            # emptied of canvases) and no late layoutstop handler may touch the destroyed instance.
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
            assert not page_errors, page_errors
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
def test_graph_tier_switches_keep_exactly_one_risk_picker(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#graph",
                    wait_until="networkidle",
                )
                picker = page.locator('select[aria-label="risk assessment"]')
                assert picker.count() == 1

                page.get_by_role("button", name="Full graph (debug)", exact=True).click()
                page.wait_for_function("() => window.__pebraGraph.snapshot().mode === 'symbol'")
                assert picker.count() == 1

                page.get_by_role("button", name="God map", exact=True).click()
                page.wait_for_function("() => window.__pebraGraph.snapshot().mode === 'godmap'")
                picker.wait_for(state="visible")
                assert picker.count() == 1
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_zoom_controls_report_truthful_levels(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")

            initial = page.evaluate("() => window.__pebraGraph.snapshot().zoom")
            page.get_by_role("button", name="Zoom out", exact=True).click()
            zoomed_out = page.evaluate("() => window.__pebraGraph.snapshot().zoom")
            assert zoomed_out < initial

            page.get_by_role("button", name="Zoom in", exact=True).click()
            zoomed = page.evaluate("() => window.__pebraGraph.snapshot().zoom")
            assert zoomed > zoomed_out

            page.get_by_role("button", name="Reset zoom to 100%", exact=True).click()
            reset = page.evaluate("() => window.__pebraGraph.snapshot().zoom")
            assert reset == pytest.approx(1.0)
            browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_auto_layout_animates_once_and_respects_reduced_motion(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
                page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")
                page.get_by_role("button", name="Auto", exact=True).click()
                page.wait_for_function(
                    "() => window.__pebraGraph.snapshot().lastLayout?.name === 'cose'"
                )
                animated = page.evaluate("() => window.__pebraGraph.snapshot().lastLayout")
                assert animated["animate"] is True
                assert animated["duration"] == 600

                reduced = browser.new_context(reduced_motion="reduce")
                reduced_page = reduced.new_page()
                reduced_page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle"
                )
                reduced_page.wait_for_function(
                    "() => window.__pebraGraph && window.__pebraGraph.snapshot"
                )
                reduced_page.get_by_role("button", name="Auto", exact=True).click()
                reduced_page.wait_for_function(
                    "() => window.__pebraGraph.snapshot().lastLayout?.name === 'cose'"
                )
                still = reduced_page.evaluate("() => window.__pebraGraph.snapshot().lastLayout")
                assert still["animate"] is False
                assert still["duration"] == 0
                reduced.close()
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_activity_skeleton_is_initial_only_and_never_replays_on_live_tick(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                overview_calls = 0

                def delay_overview(route) -> None:
                    nonlocal overview_calls
                    overview_calls += 1
                    time.sleep(0.35)
                    route.continue_()

                page.route("**/api/repos/r/overview*", delay_overview)
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok&live=1#activity",
                    wait_until="domcontentloaded",
                )
                page.wait_for_selector("#view-activity .skeleton-block", state="visible", timeout=2000)
                assert page.locator("#view-activity").get_attribute("aria-busy") == "true"
                page.wait_for_selector(
                    '#view-activity[data-loaded="true"]', state="visible", timeout=5000
                )
                assert page.locator("#view-activity .skeleton-block").count() == 0
                assert page.locator("#view-activity").get_attribute("aria-busy") == "false"

                page.evaluate(
                    """() => {
                      window.__skeletonReplayed = false;
                      new MutationObserver(() => {
                        if (document.querySelector("#view-activity .skeleton-block")) {
                          window.__skeletonReplayed = true;
                        }
                      }).observe(document.querySelector("#view-activity"), {
                        childList: true, subtree: true
                      });
                    }"""
                )
                page.wait_for_timeout(2200)
                assert overview_calls >= 2
                assert page.evaluate("() => window.__skeletonReplayed") is False
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_activity_live_refresh_preserves_scroll_across_multiple_ticks(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1100, "height": 600})
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok&live=1#activity",
                    wait_until="networkidle",
                )
                page.wait_for_selector('#view-activity[data-loaded="true"]', state="visible")
                row = page.locator("#view-activity tr.clickable").first
                assessment_id = row.locator("td").first.inner_text()
                row.locator("td").nth(6).click()
                detail = page.locator('[data-testid="assessment-detail"]')
                page.wait_for_function(
                    "([node, id]) => node.textContent.includes(id)",
                    arg=[detail.element_handle(), assessment_id],
                )
                page.evaluate("window.scrollTo(0, 300)")
                old_scroll = page.evaluate("window.scrollY")
                assert old_scroll == 300

                page.wait_for_timeout(3400)

                assert page.evaluate("window.scrollY") == old_scroll
                assert assessment_id in detail.inner_text()
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_live_refresh_preserves_camera_selection_and_controls(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page_errors: list[str] = []
                learning_calls = 0

                def fail_later_learning_refresh(route) -> None:
                    nonlocal learning_calls
                    learning_calls += 1
                    if learning_calls == 1:
                        route.continue_()
                    else:
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body='{"status":"available","items":"malformed"}',
                        )

                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.route("**/api/repos/r/learning/context*", fail_later_learning_refresh)
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok&live=1#graph",
                    wait_until="networkidle",
                )
                page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")

                page.fill(".graph-search", "class")
                page.wait_for_selector(".search-row")
                page.click(".search-row")
                page.wait_for_timeout(400)
                page.get_by_role("button", name="Zoom in", exact=True).click()
                page.evaluate(
                    """() => {
                      document.activeElement.blur();
                      window.__liveControlRefs = {
                        auto: document.querySelector(".layout-group button"),
                        risk: document.querySelector(".overlay-toggle button"),
                      };
                    }"""
                )
                before = page.evaluate("() => window.__pebraGraph.snapshot()")

                page.wait_for_timeout(2200)
                after = page.evaluate("() => window.__pebraGraph.snapshot()")
                controls_survived = page.evaluate(
                    """() => window.__liveControlRefs.auto
                      === document.querySelector(".layout-group button")
                      && window.__liveControlRefs.risk
                      === document.querySelector(".overlay-toggle button")"""
                )

                assert learning_calls >= 2
                assert before["selected"] == ["n:b"]
                assert after["selected"] == before["selected"]
                assert after["zoom"] == pytest.approx(before["zoom"])
                assert after["pan"]["x"] == pytest.approx(before["pan"]["x"])
                assert after["pan"]["y"] == pytest.approx(before["pan"]["y"])
                assert controls_survived is True
                assert not page_errors, page_errors
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_graph_live_refresh_renders_a_changed_codegraph_snapshot(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    reader = _ChangingGraphReader()
    with _serve(db, reader=reader, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok&live=1#graph",
                    wait_until="networkidle",
                )
                page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")
                assert len(page.evaluate("() => window.__pebraGraph.snapshot().nodes")) == 5
                page.evaluate(
                    "() => { window.__autoControl = document.querySelector('.layout-group button'); }"
                )

                page.wait_for_function(
                    "() => window.__pebraGraph.snapshot().nodes.some(n => n.id === 'n:new')",
                    timeout=5000,
                )

                assert reader.godmap_calls >= 2
                assert page.evaluate(
                    "() => window.__autoControl === document.querySelector('.layout-group button')"
                )
            finally:
                browser.close()


@pytest.mark.skipif(not _chromium_available(), reason="playwright Chromium browser not installed")
def test_activity_table_breakpoint_keeps_hidden_fields_keyboard_reachable(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 899, "height": 700})
                page.goto(
                    f"http://127.0.0.1:{port}/?repo=r&token=tok#activity",
                    wait_until="networkidle",
                )
                page.wait_for_selector('#view-activity[data-loaded="true"]', state="visible")
                fingerprint_header = page.locator("#view-activity thead th").nth(3)
                assert fingerprint_header.evaluate("node => getComputedStyle(node).display") == "none"

                action = page.locator("#view-activity .assessment-link").first
                action.focus()
                action.press("Enter")
                detail = page.locator('[data-testid="assessment-detail"]')
                page.wait_for_function(
                    "node => node.textContent.includes('fingerprint') && node.textContent.includes('rau')",
                    arg=detail.element_handle(),
                )
                detail_text = detail.inner_text().lower()
                assert "assessment" in detail_text
                assert "edit confidence" in detail_text
                assert "lesson" in detail_text

                page.set_viewport_size({"width": 901, "height": 700})
                assert fingerprint_header.evaluate("node => getComputedStyle(node).display") != "none"
            finally:
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
def test_godmap_symbol_labels_reveal_only_after_zooming_in(tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    db = _seed(tmp_path)
    with _serve(db, dev_mode=True) as port:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/?repo=r&token=tok#graph", wait_until="networkidle")
            page.wait_for_function("() => window.__pebraGraph && window.__pebraGraph.snapshot")

            zoom_out = page.get_by_role("button", name="Zoom out", exact=True)
            for _ in range(8):
                zoom_out.click()
            low_zoom = page.evaluate("() => window.__pebraGraph.snapshot()")
            low_by_id = {n["id"]: n for n in low_zoom["nodes"]}
            assert low_by_id["file:a.py"]["label"] == "a.py"
            assert low_by_id["n:a"]["label"] == ""

            page.get_by_role("button", name="Reset zoom to 100%", exact=True).click()
            page.wait_for_function(
                "() => window.__pebraGraph.snapshot().nodes"
                ".find(n => n.id === 'n:a').label === 'a'"
            )
            high_zoom = page.evaluate("() => window.__pebraGraph.snapshot()")
            high_by_id = {n["id"]: n for n in high_zoom["nodes"]}
            assert high_by_id["file:a.py"]["label"] == "a.py"
            assert high_by_id["n:a"]["label"] == "a"
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
