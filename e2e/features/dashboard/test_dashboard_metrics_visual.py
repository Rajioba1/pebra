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
import urllib.request

import pytest

from e2e.utils import dashboard_harness as dh

_E2E_UI = os.environ.get("E2E_UI") == "1"
_TABS = ("overview", "history", "calibration", "learning", "graph")


def _api_get(port: int, token: str, path: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read())


def test_dashboard_serves_learned_run_metrics_on_local_port(seeded_learning_state):
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
        assert info.port > 0 and info.token
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
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
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

    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
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
                page.goto(info.url + "&live=1")
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
