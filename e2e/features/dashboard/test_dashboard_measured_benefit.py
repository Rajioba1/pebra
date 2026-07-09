"""Phase 3 (#2 verify leg + #4 machine assertion): a scripted assess -> apply -> verify(RCA) flow leaves
a MEASURED maintainability benefit that the dashboard exposes end-to-end.

This is the leg the seeded-learning scenario doesn't cover (it never calls `verify`). CLI/HTTP-only (no
`import pebra`); gated on the rust-code-analysis-cli binary. It proves the same data the History
"Measured benefit detail" drill-in renders: complexity down, MI up, measured_benefit > 0.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from e2e.utils import cli_harness as ch
from e2e.utils import dashboard_harness as dh


def _rca_present() -> bool:
    override = os.environ.get("PEBRA_RCA_BIN", "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return True
        if p.is_dir():
            return any((p / n).is_file() for n in ("rust-code-analysis-cli.exe", "rust-code-analysis-cli"))
    return shutil.which("rust-code-analysis-cli") is not None


pytestmark = pytest.mark.skipif(not _rca_present(), reason="rust-code-analysis-cli not installed")
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


def _repo_id(repo: Path) -> str:
    return "repo_" + hashlib.sha1(str(repo.resolve()).encode("utf-8")).hexdigest()[:12]


def _request(path: Path, repo: Path, filename: str, patch: str) -> Path:
    request = {
        "schema_version": "0.1", "task": "measured benefit dashboard", "repo_id": _repo_id(repo),
        "candidate_actions": [{"id": "a1", "label": "edit", "action_type": "edit",
                               "expected_files": [filename], "proposed_patch": patch}],
        "evidence": {"p_success": 0.9, "immediate_benefit": 0.3, "review_cost": 0.1,
                     "criticality_stage": "C1", "criticality_value": 0.2,
                     "benefit_delta_evidence": {"source_type": "projected",
                                                "future_change_exposure": 0.1, "deltas": {}}},
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
    req = _request(tmp_path / "r.json", repo, "calc.py", _patch("calc.py", _BEFORE, _SIMPLER))
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
    req = _request(tmp_path / "r.json", repo, "calc.py", _patch("calc.py", _BEFORE, _SIMPLER))
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
                panel = page.locator("text=Measured benefit detail").locator("xpath=ancestor::section")
                assert "complexity_delta" in panel.inner_text()
                assert "maintainability_index_delta" in panel.inner_text()
                assert "No verify / measured-benefit" not in panel.inner_text()
            finally:
                browser.close()
