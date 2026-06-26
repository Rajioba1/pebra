"""Phase 3b/5c-C/D — Risk Observatory FastAPI app: bearer auth, Host allowlist, CSP, read routes.

Needs fastapi/jinja2/httpx -> nox only.
"""

from __future__ import annotations

import importlib.util

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("fastapi", "jinja2", "httpx")),
    reason="requires fastapi/jinja2/httpx (run via nox)",
)


def _seed(tmp_path) -> tuple[str, str]:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"edit_confidence": 0.83},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "t"},
    )
    store.close()
    return db, asm


def _client(db: str, token: str = "tok", base: str = "http://127.0.0.1"):
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    return TestClient(create_app(db, token), base_url=base)


_AUTH = {"Authorization": "Bearer tok"}


def test_index_served_with_csp_nonce(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp
    nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
    assert nonce in resp.text  # same nonce on the inline <script> tag


def test_api_requires_bearer(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    assert _client(db).get("/api/chain-status").status_code == 401


def test_api_with_bearer_returns_chain_status(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/chain-status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_assessments_route_lists_seeded(tmp_path) -> None:
    db, asm = _seed(tmp_path)
    resp = _client(db).get("/api/repos/r/assessments", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["assessment_id"] == asm


def test_overview_route_counts_decisions(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/repos/r/overview", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["by_decision"]["proceed"] == 1
    assert body["by_status"]["pending"] == 1


def test_detail_unknown_assessment_404(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/assessments/asm_999", headers=_AUTH)
    assert resp.status_code == 404


def test_forbidden_host_is_rejected(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db, base="http://evil.attacker.com").get("/")
    assert resp.status_code == 403


def test_hostname_parses_ipv4_ipv6_and_ports() -> None:
    from pebra.dashboard.server import _hostname

    assert _hostname("127.0.0.1") == "127.0.0.1"
    assert _hostname("127.0.0.1:9473") == "127.0.0.1"
    assert _hostname("localhost") == "localhost"
    assert _hostname("[::1]") == "::1"  # bare IPv6 loopback no longer mangled
    assert _hostname("[::1]:9473") == "::1"
    assert _hostname("evil.com:80") == "evil.com"


def test_static_served_without_bearer(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/static/app.js")
    assert resp.status_code == 200
