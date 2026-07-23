"""Phase E4 (+ 5d): the dashboard serves all metric views on a local port, and a human — or a headless
browser — can see them.

Two layers, both CLI/HTTP-only (never `import pebra`):
  - `test_dashboard_serves_*` (ungated): launches the REAL dashboard and GETs every read endpoint over
    HTTP with the bearer token (stdlib urllib). Deterministic; proves the metrics-on-a-port boundary.
  - `test_dashboard_visual_*` (E2E_UI=1): drives the page with Playwright across all five tabs, asserts
    NO Content-Security-Policy violations and NO uncaught page errors (turning "did we keep the strict
    CSP" into an automated gate), and saves a screenshot per view for human review.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from e2e.utils import dashboard_harness as dh

_E2E_UI = os.environ.get("E2E_UI") == "1"
_TABS = ("overview", "history", "calibration", "learning", "graph")


def _with_live(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}live=1"


def _api_get(port: int, token: str, path: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}  # loopback default is token-free
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read())


def test_dashboard_url_parser_accepts_queryless_loopback_url():
    match = dh._URL_RE.search("PEBRA Risk Observatory: http://127.0.0.1:4500/")
    assert match is not None
    assert match.group(1) == "http://127.0.0.1:4500/"
    assert match.group(2) == "4500"


def test_live_url_appends_query_separator():
    assert _with_live("http://127.0.0.1:4500/") == "http://127.0.0.1:4500/?live=1"
    assert _with_live("http://127.0.0.1:4500/?repo=r1") == (
        "http://127.0.0.1:4500/?repo=r1&live=1"
    )


def test_dashboard_loopback_default_is_token_free(seeded_learning_state):
    # The relaxed default: on loopback the URL carries no token and the API is reachable without one.
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
        assert info.token == ""  # no ?token= in the printed URL
        assert _api_get(info.port, "", "/api/chain-status")["valid"] is True  # no bearer needed


def test_dashboard_forced_token_mode_requires_bearer(seeded_learning_state):
    # --auth token forces a bearer even on loopback: URL carries it, and a request without it 401s.
    with dh.running_dashboard(
        seeded_learning_state.repo_path, seeded_learning_state.db_path, auth="token"
    ) as info:
        assert info.token  # token present in the printed URL
        req = urllib.request.Request(f"http://127.0.0.1:{info.port}/api/chain-status")
        try:
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 - loopback only
            unauthorized = False
        except urllib.error.HTTPError as exc:
            unauthorized = exc.code == 401
        assert unauthorized  # no bearer -> rejected
        assert _api_get(info.port, info.token, "/api/chain-status")["valid"] is True  # with bearer -> ok


def test_dashboard_serves_learned_run_metrics_on_local_port(seeded_learning_state):
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
        assert info.port > 0
        chain = _api_get(info.port, info.token, "/api/chain-status")
        assert chain["valid"] is True
        assert chain["counts"]["assessments"] >= 100
        assert chain["counts"]["risk_snapshots"] >= 1

        overview = _api_get(info.port, info.token, f"/api/repos/{info.repo_id}/overview")
        assert overview["total"] >= 100
        assert overview["by_decision"]["proceed"] >= 1
        assert overview["by_status"]["completed"] >= 100

        history = _api_get(info.port, info.token, f"/api/repos/{info.repo_id}/assessments?limit=5")
        assert history["items"]
        assert {"assessment_id", "decision", "terminal_status", "scores"} <= set(history["items"][0])
        assert "edit_confidence" in history["items"][0]["scores"]


def test_dashboard_serves_new_metric_endpoints(seeded_learning_state):
    # The Phase-5d read surface, over the real CLI/HTTP boundary (no binary needed; graph is fail-soft).
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path, dev=True) as info:
        rid = info.repo_id

        series = _api_get(info.port, info.token, f"/api/repos/{rid}/scores-series?limit=10")
        assert series["items"] and "benefit" in series["items"][0]["scores"]

        calib = _api_get(info.port, info.token, f"/api/repos/{rid}/calibration?target_type=risk_binary")
        assert calib["target_type"] == "risk_binary" and len(calib["bins"]) == 10

        snaps = _api_get(info.port, info.token, f"/api/repos/{rid}/learning/snapshots")
        assert isinstance(snaps["items"], list)

        # Graph routes must be fail-soft (200 + available:false) when no codegraph index is present.
        overview = _api_get(info.port, info.token, f"/api/repos/{rid}/graph/overview")
        assert "available" in overview
        if overview["available"]:
            assert isinstance(overview["files"], list)
            assert isinstance(overview["truncated"], bool)
            assert isinstance(overview["total_file_count"], int)
        else:
            assert overview["files"] == []


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_dashboard_visual_all_views_no_csp_violations(seeded_learning_state, out_dir):
    from playwright.sync_api import sync_playwright

    shots = out_dir / "screenshots"
    csp_errors: list[str] = []
    page_errors: list[str] = []

    with dh.running_dashboard(
        seeded_learning_state.repo_path, seeded_learning_state.db_path, dev=True
    ) as info:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()

                def _on_console(msg) -> None:
                    text = msg.text or ""
                    if msg.type == "error" and (
                        "Content Security Policy" in text or "Refused to" in text
                    ):
                        csp_errors.append(text)

                page.on("console", _on_console)
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))

                # ?live=1 exercises the poll loop + the live indicator.
                page.goto(_with_live(info.url))
                page.wait_for_selector('[data-testid="chain-status"]', timeout=15000)
                # ?live=1 must REMOVE the boolean `hidden` attribute (is_visible), not merely leave a
                # falsy value — `get_attribute("hidden")` returns "" when present, so test visibility.
                assert page.locator("#live-dot").is_visible()

                for tab in _TABS:
                    page.click(f'[data-tab="{tab}"]')
                    page.wait_for_selector(
                        f'[data-testid="{tab}"][data-loaded="true"]', state="visible", timeout=15000
                    )
                    assert "Error loading" not in page.locator(f'[data-testid="{tab}"]').inner_text()
                    page.screenshot(path=str(shots / f"dashboard_{tab}.png"))

                assert "valid" in page.locator('[data-testid="chain-status"]').inner_text().lower()
            finally:
                browser.close()

    assert csp_errors == [], f"CSP violations in the browser console: {csp_errors}"
    assert page_errors == [], f"uncaught page errors: {page_errors}"
    assert (shots / "dashboard_graph.png").exists()  # human reviews these side-by-side


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_dashboard_live_refresh_preserves_interaction_state(seeded_learning_state):
    from playwright.sync_api import sync_playwright

    with dh.running_dashboard(
        seeded_learning_state.repo_path, seeded_learning_state.db_path, dev=True
    ) as info:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1100, "height": 600})
                page.goto(_with_live(info.url))

                page.click('[data-tab="calibration"]')
                page.wait_for_selector(
                    '[data-testid="calibration"][data-loaded="true"]', state="visible"
                )
                target = page.locator('[aria-label="target"]')
                target.focus()
                target.evaluate("node => { node.dataset.refreshIdentity = 'preserved'; }")
                page.wait_for_timeout(2000)  # one LIVE_MS poll must not replace the focused control
                assert target.get_attribute("data-refresh-identity") == "preserved"
                assert target.evaluate("node => document.activeElement === node") is True

                page.click('[data-tab="history"]')
                page.wait_for_selector('[data-testid="history"][data-loaded="true"]', state="visible")
                row = page.locator("#view-history tr.clickable").first
                assessment_id = row.locator("td").first.inner_text()
                row.click()
                detail = page.locator('[data-testid="assessment-detail"]')
                page.wait_for_function(
                    "([node, id]) => node.textContent.includes(id)",
                    arg=[detail.element_handle(), assessment_id],
                )
                page.evaluate("window.scrollTo(0, 300)")
                old_scroll = page.evaluate("window.scrollY")
                assert old_scroll > 0

                page.wait_for_timeout(2000)

                assert assessment_id in detail.inner_text()
                assert page.evaluate("window.scrollY") == old_scroll
            finally:
                browser.close()


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_agent_ab_observatory_refresh_preserves_interaction_state():
    from playwright.sync_api import sync_playwright

    app_js = (
        Path(__file__).resolve().parents[2]
        / "experiments"
        / "agent_ab"
        / "runners"
        / "observatory"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 600, "height": 300})
            page.set_content(
                '<span id="poll"></span><main id="app"><button id="active">copy</button></main>'
            )
            page.add_script_tag(content=app_js)

            active = page.locator("#active")
            active.evaluate("node => { node.dataset.focusKey = 'copy:run|clone'; }")
            active.focus()
            page.evaluate(
                """() => {
                    const replacement = document.createElement("button");
                    replacement.id = "replacement";
                    replacement.dataset.focusKey = "copy:run|clone";
                    replaceApp(document.getElementById("app"), [replacement]);
                }"""
            )
            replacement = page.locator("#replacement")
            assert replacement.evaluate("node => document.activeElement === node") is True

            page.evaluate(
                """() => {
                    const app = document.getElementById("app");
                    const details = document.createElement("details");
                    details.dataset.stateKey = "diagnostics";
                    details.open = true;
                    const scroller = document.createElement("div");
                    scroller.className = "table-scroll";
                    scroller.style.width = "100px";
                    scroller.style.overflow = "auto";
                    const wide = document.createElement("div");
                    wide.style.width = "500px";
                    wide.style.height = "700px";
                    scroller.appendChild(wide);
                    app.replaceChildren(details, scroller);
                    scroller.scrollLeft = 70;
                    window.scrollTo(0, 120);

                    const nextDetails = details.cloneNode(true);
                    nextDetails.open = false;
                    const nextScroller = scroller.cloneNode(true);
                    replaceApp(app, [nextDetails, nextScroller]);
                }"""
            )

            assert page.locator('details[data-state-key="diagnostics"]').get_attribute("open") == ""
            assert page.locator(".table-scroll").evaluate("node => node.scrollLeft") == 70
            assert page.evaluate("window.scrollY") == 120
        finally:
            browser.close()
