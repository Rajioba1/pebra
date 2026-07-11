"""Phase 3 (#2 verify leg + #4 machine assertion): a scripted assess -> apply -> verify(RCA) flow leaves
a MEASURED maintainability benefit that the dashboard exposes end-to-end.

This is the leg the seeded-learning scenario doesn't cover (it never calls `verify`). CLI/HTTP-only (no
`import pebra`); gated on the rust-code-analysis-cli binary. It proves the same data the History
"Measured benefit detail" drill-in renders: complexity down, MI up, measured_benefit > 0.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

from e2e.utils import cli_harness as ch
from e2e.utils import dashboard_harness as dh
from e2e.utils import rca_probe

pytestmark = pytest.mark.skipif(
    rca_probe.find_rca() is None, reason="rust-code-analysis-cli not installed"
)
_E2E_UI = os.environ.get("E2E_UI") == "1"

_BEFORE = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"
_SIMPLER = "def f(x):\n    return abs(x)\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _repo(dest: Path, filename: str, content: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / filename).write_text(content, encoding="utf-8")
    _git(dest, "init", "-q")
    _git(dest, "config", "user.email", "e2e@pebra.test")
    _git(dest, "config", "user.name", "pebra-e2e")
    _git(dest, "add", ".")
    _git(dest, "commit", "-q", "-m", "seed")
    return dest


def _patch(filename: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=filename, tofile=filename))


def _git_patch(filename: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{filename}", tofile=f"b/{filename}"))


def _request(path: Path, filename: str, patch: str) -> Path:
    # repo_id in the request body is inert: the assess path resolves repo_id from --repo-root and never
    # reads this field (it only requires presence), so a literal placeholder avoids duplicating the
    # production hash formula (which would silently drift). The real repo_id used below comes from the
    # dashboard's own printed URL (info.repo_id), i.e. genuine production output.
    request = {
        "schema_version": "0.1", "task": "measured benefit dashboard", "repo_id": "repo_e2e_placeholder",
        "candidate_actions": [{"id": "a1", "label": "edit", "action_type": "edit",
                               "expected_files": [filename], "proposed_patch": patch}],
        "evidence": {"p_success": 0.9, "immediate_benefit": 0.3, "review_cost": 0.1,
                     "criticality_stage": "C1", "criticality_value": 0.2,
                     "benefit_delta_evidence": {"source_type": "projected",
                                                "future_change_exposure": 0.1, "deltas": {}}},
    }
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _revise_safer_request(path: Path, filename: str, patch: str) -> Path:
    request = {
        "schema_version": "0.1",
        "task": "change a public API with material benefit",
        "repo_id": "repo_e2e_placeholder",
        "candidate_actions": [{
            "id": "a1",
            "label": "change public_api contract",
            "action_type": "edit",
            "expected_files": [filename],
            "proposed_patch": patch,
        }],
        "evidence": {
            "events": [{
                "event": "public_api_break",
                "p_event": 0.60,
                "elicited_disutility": 0.80,
            }],
            "p_success": 0.80,
            "immediate_benefit": 0.50,
            "review_cost": 0.10,
            "criticality_stage": "C2",
            "criticality_value": 0.40,
            "symbol_diff": {
                "parsed_patch_available": True,
                "changed_symbols": [f"{filename}::public_api"],
                "max_change_kind": "CONTRACT",
                "visibility": "public_api",
                "consequential_symbol_changed": True,
            },
            "benefit_delta_evidence": {
                "source_type": "projected",
                "future_change_exposure": 0.0,
                "deltas": {},
            },
        },
        "thresholds": {
            "max_expected_loss_without_human": 0.20,
            "revise_safer_enabled": True,
            "revise_safer_attempt": 0,
            "max_revise_safer_attempts": 1,
        },
    }
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _api_get(port: int, token: str, path: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read())


def test_dashboard_exposes_measured_rca_benefit(tmp_path):
    repo = _repo(tmp_path / "repo", "calc.py", _BEFORE)
    db = tmp_path / "p.db"
    req = _request(tmp_path / "r.json", "calc.py", _patch("calc.py", _BEFORE, _SIMPLER))
    asm = ch.assess(req, repo_root=repo, db=db)["assessment_id"]

    (repo / "calc.py").write_text(_SIMPLER, encoding="utf-8")  # apply the simplification for real
    _git(repo, "add", "calc.py")
    _passed, payload = ch.verify(asm, repo_root=repo, db=db, scope="staged")
    assert not payload.get("classification_failed"), payload
    assert payload.get("measured_benefit_deltas"), payload  # fires RCA -> persists measured benefit

    with dh.running_dashboard(repo, db) as info:
        detail = _api_get(info.port, info.token, f"/api/repos/{info.repo_id}/assessments/{asm}")
        graded = [g for g in detail["guardrails"] if g.get("measured_benefit_deltas")]
        assert graded, "verify should have persisted a measured-benefit guardrails row"
        g = graded[-1]
        assert g["measured_benefit_deltas"]["complexity_delta"] < 0            # fewer branches
        assert g["measured_benefit_deltas"]["maintainability_index_delta"] > 0  # more maintainable
        assert g["measured_benefit"] > 0


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_dashboard_history_renders_measured_benefit_detail(tmp_path):
    from playwright.sync_api import sync_playwright

    repo = _repo(tmp_path / "repo", "calc.py", _BEFORE)
    db = tmp_path / "p.db"
    req = _request(tmp_path / "r.json", "calc.py", _patch("calc.py", _BEFORE, _SIMPLER))
    asm = ch.assess(req, repo_root=repo, db=db)["assessment_id"]

    (repo / "calc.py").write_text(_SIMPLER, encoding="utf-8")
    _git(repo, "add", "calc.py")
    _passed, payload = ch.verify(asm, repo_root=repo, db=db, scope="staged")
    assert not payload.get("classification_failed"), payload
    assert payload.get("measured_benefit_deltas"), payload

    with dh.running_dashboard(repo, db) as info:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(info.url)
                page.wait_for_selector('[data-testid="chain-status"]', timeout=15000)
                page.click('[data-tab="history"]')
                page.wait_for_selector('[data-testid="history"][data-loaded="true"]', timeout=15000)
                page.locator("tr.clickable").first.click()
                page.wait_for_selector("text=maintainability_index_delta", timeout=15000)
                panel = page.get_by_text("Measured benefit detail", exact=True).locator("xpath=..")
                assert "complexity_delta" in panel.inner_text()
                assert "maintainability_index_delta" in panel.inner_text()
                assert "No verify / measured-benefit" not in panel.inner_text()
            finally:
                browser.close()


@pytest.mark.skipif(not _E2E_UI, reason="E2E_UI not set (needs pebra[ui-e2e] + playwright install)")
def test_dashboard_history_renders_revise_safer_risk_benefit_math(tmp_path):
    from playwright.sync_api import sync_playwright

    before = "def public_api(x):\n    return x\n"
    after = "def public_api(x, y):\n    return x + y\n"
    repo = _repo(tmp_path / "repo", "api.py", before)
    db = tmp_path / "p.db"
    req = _revise_safer_request(
        tmp_path / "revise.json", "api.py", _git_patch("api.py", before, after)
    )
    payload = ch.assess(req, repo_root=repo, db=db)

    assert payload["recommended_decision"] == "revise_safer"
    scores = payload["scores"]
    for key in ("expected_loss", "benefit", "expected_utility", "rau"):
        assert math.isfinite(scores[key])
    assert scores["expected_loss"] > scores["effective_threshold"]
    assert scores["benefit"] > 0
    assert scores["expected_utility"] < 0

    with dh.running_dashboard(repo, db) as info:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(info.url)
                page.wait_for_selector('[data-testid="chain-status"]', timeout=15000)
                page.click('[data-tab="history"]')
                page.wait_for_selector(
                    '[data-testid="history"][data-loaded="true"]', timeout=15000
                )
                row = page.locator("tr.clickable").filter(has_text=payload["assessment_id"])
                cells = row.locator("td")
                assert cells.nth(1).inner_text() == "revise_safer"
                assert cells.nth(2).inner_text() == f'{scores["expected_loss"]:.3f}'
                assert cells.nth(3).inner_text() == f'{scores["benefit"]:.3f}'
                assert cells.nth(4).inner_text() == f'{scores["rau"]:.3f}'
            finally:
                browser.close()
