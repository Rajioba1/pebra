"""Phase E4: the dashboard serves metrics on a local port, and a human can verify it visually.

Two layers:
  - `test_dashboard_serves_metrics_on_local_port` (ungated): launches the REAL dashboard on an OS port,
    GETs the metrics API over HTTP with the bearer token (stdlib urllib — no extra dep). Proves the
    metrics-on-a-port boundary deterministically.
  - `test_dashboard_visual_screenshot` (E2E_UI=1): drives the page with Playwright, waits on the
    data-testid sections, and saves a screenshot to out/screenshots for human side-by-side review.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from e2e.utils import dashboard_harness as dh

_E2E_UI = os.environ.get("E2E_UI") == "1"


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


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_dashboard_visual_screenshot(seeded_learning_state, out_dir):
    from playwright.sync_api import sync_playwright

    shot = out_dir / "screenshots" / "dashboard_overview.png"
    with dh.running_dashboard(seeded_learning_state.repo_path, seeded_learning_state.db_path) as info:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(info.url)
                page.wait_for_selector('[data-testid="chain-status"]', timeout=15000)
                page.wait_for_selector('[data-testid="overview"]', timeout=15000)
                page.wait_for_selector('[data-testid="history"]', timeout=15000)
                page.screenshot(path=str(shot))
                assert "valid" in page.inner_text('[data-testid="chain-status"]').lower()
            finally:
                browser.close()
    assert shot.exists()  # human reviews this side-by-side with the baseline
