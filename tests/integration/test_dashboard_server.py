"""Phase 3b/5c-C/D — Risk Observatory FastAPI app: bearer auth, Host allowlist, CSP, read routes.

Needs fastapi/jinja2/httpx -> nox only.
"""

from __future__ import annotations

import importlib.util

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("fastapi", "jinja2", "httpx")),
    reason="requires fastapi/jinja2/httpx (run via nox)",
)


def _seed(tmp_path) -> tuple[str, str]:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = _persist_assessment(store, repo_id="r")
    store.close()
    return db, asm


def _persist_assessment(
    store: SqliteStore, *, repo_id: str = "r", predictions: list[dict] | None = None
) -> str:
    return store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"edit_confidence": 0.83},
            repo_id=repo_id,
            repo_root="/x",
            model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "t"},
        predictions=predictions,
    )


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


def test_calibration_route_accepts_review_cost_target(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get(
        "/api/repos/r/calibration?target_type=cost_continuous&scope=all", headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["target_type"] == "cost_continuous"


def test_dashboard_calibration_control_lists_review_cost(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert '["cost_continuous", "review cost (continuous)"]' in text


def test_assessments_route_lists_seeded(tmp_path) -> None:
    db, asm = _seed(tmp_path)
    resp = _client(db).get("/api/repos/r/assessments", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["assessment_id"] == asm


def test_learning_routes_characterize_current_items_envelope_and_auth(tmp_path) -> None:
    """Milestone 0 characterization: lock the current /learning/snapshots and /learning/facts JSON
    envelope + bearer enforcement before Milestone 3 rewires them through the shared controller.
    Milestone 3 must preserve this byte-equivalent envelope."""
    db, _ = _seed(tmp_path)
    client = _client(db)
    for route in ("/api/repos/r/learning/snapshots", "/api/repos/r/learning/facts"):
        assert client.get(route).status_code == 401  # bearer required
        resp = client.get(route, headers=_AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": []}


@pytest.mark.xfail(strict=True, reason="Milestone 5C: /learning/context route not implemented yet")
def test_learning_context_route_serves_verified_lessons(tmp_path) -> None:
    """Milestone 0 forward spec for Milestone 5C: a repo-scoped, bearer-guarded /learning/context
    route exposes verified lessons through the same {'items': [...]} envelope."""
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/repos/r/learning/context", headers=_AUTH)
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_dashboard_and_tui_return_identical_assessment_identity_fields(tmp_path) -> None:
    from pebra.observatory_context import ObservatoryContext
    from pebra.tui.data import ObservatoryData

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={
                "binding": {
                    "candidate": {
                        "algorithm": CANDIDATE_BINDING_ALGORITHM,
                        "files": {"src/auth.py": "a" * 64},
                    }
                }
            },
        ),
        {
            "task": "Fix authentication",
            "action_id": "edit-auth",
            "revision_envelope": {"expected_files": ["src/auth.py"]},
        },
    )
    store.close()

    dashboard_row = _client(db).get("/api/repos/r/assessments", headers=_AUTH).json()["items"][0]
    tui_row = ObservatoryData(
        ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)
    ).refresh_snapshot().assessments[0]

    assert dashboard_row == tui_row
    assert dashboard_row["target_files"] == ["src/auth.py"]
    assert dashboard_row["target_provenance"] == "candidate_bound"


def test_authenticated_assessments_route_survives_invalid_utf8_identity(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={
                "binding": {
                    "candidate": {
                        "algorithm": CANDIDATE_BINDING_ALGORITHM,
                        "files": {"src/bound.py": "a" * 64},
                        "metadata": {"note": "bad\ud800metadata"},
                    }
                }
            },
        ),
        {
            "task": "bad\ud800task",
            "action_id": "bad\udfffaction",
            "revision_envelope": {
                "expected_files": ["src/bad\ud800.py", "src/good.py"]
            },
        },
    )
    store.close()

    response = _client(db).get("/api/repos/r/assessments", headers=_AUTH)

    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["assessment_id"] == asm
    assert row["task"] is None
    assert row["action_id"] is None
    assert row["target_files"] == ["src/good.py"]
    assert row["candidate_fingerprint"] is None


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


def test_repo_scoped_assessment_detail_rejects_foreign_repo(tmp_path) -> None:
    db, asm = _seed(tmp_path)
    store = SqliteStore(db)
    other = _persist_assessment(store, repo_id="other")
    store.close()

    client = _client(db)
    assert client.get(f"/api/repos/r/assessments/{asm}", headers=_AUTH).status_code == 200
    assert client.get(f"/api/repos/r/assessments/{other}", headers=_AUTH).status_code == 404


def test_bound_global_assessment_detail_rejects_foreign_repo(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    store = SqliteStore(db)
    other = _persist_assessment(store, repo_id="other")
    store.close()

    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    client = TestClient(create_app(db, "tok", repo_id="r"), base_url="http://127.0.0.1")
    assert client.get(f"/api/assessments/{other}", headers=_AUTH).status_code == 404


_REPO_COLLECTION_PATHS = (
    "/api/repos/other/assessments",
    "/api/repos/other/overview",
    "/api/repos/other/scores-series",
    "/api/repos/other/calibration",
    "/api/repos/other/learning/snapshots",
    "/api/repos/other/learning/facts",
)


@pytest.mark.parametrize("path", _REPO_COLLECTION_PATHS)
def test_bound_dashboard_rejects_foreign_repo_collection_routes(tmp_path, path: str) -> None:
    db, _ = _seed(tmp_path)

    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    app = create_app(db, "tok", repo_id="r")
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get(path, headers=_AUTH)

    assert response.status_code == 404
    assert response.json() == {"detail": "repo not found"}


@pytest.mark.parametrize("path", _REPO_COLLECTION_PATHS)
def test_unbound_dashboard_preserves_multi_repo_collection_access(tmp_path, path: str) -> None:
    db, _ = _seed(tmp_path)
    store = SqliteStore(db)
    foreign = _persist_assessment(store, repo_id="other")
    store.close()

    response = _client(db).get(path, headers=_AUTH)

    assert response.status_code == 200
    if path.endswith("/assessments"):
        assert response.json()["items"][0]["assessment_id"] == foreign


# --- M1 characterization: lock the exact JSON shapes of the migrated read routes so the shared
# query-controller rewire cannot silently drift them. ---


def test_overview_shape_is_stable(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    body = _client(db).get("/api/repos/r/overview", headers=_AUTH).json()
    assert set(body) == {"total", "by_decision", "by_status", "chain"}
    assert set(body["chain"]) == {"valid", "counts"}
    assert body["by_status"] == {"pending": 1}  # None terminal_status renders as "pending"


def test_scores_series_item_shape_is_stable(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    item = _client(db).get("/api/repos/r/scores-series", headers=_AUTH).json()["items"][0]
    assert set(item) == {"assessment_id", "decision", "assessed_commit", "terminal_status", "scores"}
    assert set(item["scores"]) == {
        "expected_loss", "benefit", "expected_utility", "rau", "edit_confidence",
    }


def test_assessments_route_envelope_is_items_list(tmp_path) -> None:
    db, asm = _seed(tmp_path)
    body = _client(db).get("/api/repos/r/assessments", headers=_AUTH).json()
    assert set(body) == {"items"}
    assert isinstance(body["items"], list) and body["items"][0]["assessment_id"] == asm


def test_chain_status_route_shape_is_stable(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    body = _client(db).get("/api/chain-status", headers=_AUTH).json()
    assert set(body) == {"valid", "counts"}
    assert body["valid"] is True


def test_forbidden_host_is_rejected(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db, base="http://evil.attacker.com").get("/")
    assert resp.status_code == 403


def test_resolve_token_loopback_auto_and_none_are_none() -> None:
    from pebra.dashboard.server import resolve_dashboard_token

    assert resolve_dashboard_token("127.0.0.1", "auto") is None
    assert resolve_dashboard_token("localhost", "auto") is None
    assert resolve_dashboard_token("::1", "auto") is None
    assert resolve_dashboard_token("127.0.0.1", "none") is None


def test_resolve_token_token_mode_generates_even_on_loopback() -> None:
    from pebra.dashboard.server import resolve_dashboard_token

    t = resolve_dashboard_token("127.0.0.1", "token")
    assert isinstance(t, str) and len(t) > 10


def test_resolve_token_nonloopback_auto_generates() -> None:
    from pebra.dashboard.server import resolve_dashboard_token

    assert resolve_dashboard_token("0.0.0.0", "auto")  # a network bind must carry a token


def test_resolve_token_nonloopback_none_fails_loudly() -> None:
    from pebra.dashboard.server import resolve_dashboard_token

    with pytest.raises(ValueError, match="loopback"):
        resolve_dashboard_token("0.0.0.0", "none")


def test_resolve_token_normalizes_empty_explicit_to_none() -> None:
    # An empty explicit token must never survive as app.state.token="" (which would let an empty
    # `Bearer ` authenticate); it collapses to None (no-auth) on loopback.
    from pebra.dashboard.server import resolve_dashboard_token

    assert resolve_dashboard_token("127.0.0.1", "auto", "") is None


def test_serve_guard_rejects_network_bind_without_token() -> None:
    # Defense-in-depth at the bind point: the invariant "network bind => token" is enforced by code,
    # not just by the CLI happening to route through resolve_dashboard_token.
    from pebra.dashboard.server import _require_token_for_network_bind

    _require_token_for_network_bind("127.0.0.1", None)  # loopback, no token -> fine
    _require_token_for_network_bind("0.0.0.0", "tok")   # network + token -> fine
    with pytest.raises(ValueError, match="token"):
        _require_token_for_network_bind("0.0.0.0", None)
    with pytest.raises(ValueError, match="token"):
        _require_token_for_network_bind("0.0.0.0", "")  # empty == no token


def test_no_token_mode_serves_api_without_bearer(tmp_path) -> None:
    # The loopback-default posture: token=None => require_bearer skips, /api is open on loopback.
    db, _ = _seed(tmp_path)
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    client = TestClient(create_app(db, None), base_url="http://127.0.0.1")
    assert client.get("/api/chain-status").status_code == 200  # no Authorization header sent


def test_empty_configured_token_normalizes_to_no_auth(tmp_path) -> None:
    # An empty configured token is not a real bearer. Keep the app state in the explicit no-auth
    # posture instead of preserving token="" and letting `Authorization: Bearer ` compare equal.
    db, _ = _seed(tmp_path)
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    app = create_app(db, "")
    assert app.state.token is None
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/chain-status").status_code == 200


def test_empty_bearer_does_not_authenticate_when_token_required(tmp_path) -> None:
    # Guard the empty-string trap: an empty `Authorization: Bearer ` must NOT match a real token.
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/chain-status", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_startup_url_omits_token_when_none() -> None:
    from pebra.dashboard.server import _startup_url

    assert _startup_url("127.0.0.1", 4500, None, "r1") == "http://127.0.0.1:4500/?repo=r1"
    assert _startup_url("127.0.0.1", 4500, None, None) == "http://127.0.0.1:4500/"


def test_startup_url_includes_token_when_present() -> None:
    from pebra.dashboard.server import _startup_url

    u = _startup_url("127.0.0.1", 4500, "abc", "r1")
    assert u.startswith("http://127.0.0.1:4500/?") and "token=abc" in u and "repo=r1" in u


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


def test_assessment_detail_exposes_measured_benefit(tmp_path) -> None:
    # The History "Measured benefit detail" drill-in reads measured_benefit / measured_benefit_deltas
    # from a persisted guardrails row via the repo-scoped assessment detail route.
    db, asm = _seed(tmp_path)
    store = SqliteStore(db)
    store.persist_guardrails(asm, {
        "decision": "proceed", "measured_benefit": 0.4,
        "measured_benefit_deltas": {"complexity_delta": -2.0, "maintainability_index_delta": 3.0},
    })
    store.close()
    body = _client(db).get(f"/api/repos/r/assessments/{asm}", headers=_AUTH).json()
    g = body["guardrails"][-1]
    assert g["measured_benefit"] == 0.4
    assert g["measured_benefit_deltas"]["complexity_delta"] == -2.0
    assert g["_store"]["id"].startswith("pag_")
    assert g["_store"]["row_hash"]


def test_assessment_detail_exposes_hash_chained_prior_provenance(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = _persist_assessment(store, predictions=[{
        "action_id": "a",
        "target_type": "risk_binary",
        "target_name": "p_success",
        "predicted_value": 0.8,
        "prediction_scope": "shadow",
        "provenance": {"warm_prior": {
            "calibration_tag": "population-v1",
            "applied_fields": ["p_success", "p_success_variance"],
            "field_sources": {"p_success_variance": {"applied_variance": 0.01}},
        }},
        "features": {},
    }])
    store.close()

    body = _client(db).get(f"/api/repos/r/assessments/{asm}", headers=_AUTH).json()

    assert body["prior_provenance"]["source"] == "shipped"
    assert body["prior_provenance"]["calibration_tags"] == ["population-v1"]
    assert body["prior_provenance"]["targets"]["p_success"]["applied_variance"] == 0.01


def test_dashboard_static_wires_measured_benefit_drilldown(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert "showMeasuredBenefit" in text
    assert 'rp("/assessments/"' in text
    assert 'getJSON("/api/assessments/"' not in text
    assert "measured_benefit_deltas" in text
    assert "Prior source" in text


def test_dashboard_static_renders_expected_utility_in_history_and_chart(tmp_path) -> None:
    response = _client(tmp_path / "p.db").get("/static/app.js", headers=_AUTH)

    assert response.status_code == 200
    text = response.text
    assert '"expected utility"' in text
    assert "i.scores.expected_utility" in text


def test_dashboard_static_does_not_render_empty_guardrail_as_measured_benefit(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert "rows[rows.length - 1]" not in text


def test_dashboard_static_uses_human_metric_labels(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert "Assessments run" in text
    assert "Completed outcomes" in text
    assert "Predictions checked" in text
    assert "Learning snapshots" in text
    assert "Learned rules" in text
    assert "prediction_errors" not in text
    assert "learned_risk_facts" not in text


# --- Phase 2: richer read routes (scores-series, calibration, learning, graph) ---


def _pe_row(predicted: float, actual: int) -> dict:
    return {
        "target_type": "risk_binary", "target_name": "edit",
        "predicted_probability": predicted, "actual_outcome": actual,
        "outcome_label_status": "observed", "calibration_scope": "proceeded_edits_only",
        "shadow_mode": 0, "hash_version": 2, "benefit_guidance_influenced": 0,
    }


def _seed_rich(tmp_path) -> tuple[str, str]:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
            scores={
                "edit_confidence": 0.83, "benefit": 0.4, "rau": 0.2, "expected_utility": 0.3,
                "expected_loss": 0.1,
                "symbol_scope_evidence": {
                    "symbol_fanin": {"resolved_qualified_names": ["Gamma::Gamma", "Gamma::LogGamma"]}
                },
            },
            repo_id="r", repo_root="/x", model_guidance_packet={"decision": "proceed"},
        ),
        {"task": "t"},
    )
    store.insert_prediction_error(asm, _pe_row(0.7, 1))
    store.insert_prediction_error(asm, _pe_row(0.2, 0))
    store.insert_risk_snapshot("r", {"promotion_reason": "benefit_promoted"}, "active")
    store.close()
    return db, asm


def test_scores_series_projects_score_fields(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    body = _client(db).get("/api/repos/r/scores-series", headers=_AUTH).json()
    item = body["items"][0]
    assert item["assessment_id"] == asm
    assert item["scores"]["benefit"] == 0.4
    assert item["scores"]["rau"] == 0.2
    assert item["scores"]["edit_confidence"] == 0.83


def test_calibration_risk_binary_returns_reliability_bins(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    body = _client(db).get("/api/repos/r/calibration?target_type=risk_binary", headers=_AUTH).json()
    assert body["target_type"] == "risk_binary"
    assert len(body["bins"]) == 10
    assert body["sample_count"] == 2


def test_calibration_unknown_target_type_is_400(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    resp = _client(db).get("/api/repos/r/calibration?target_type=bogus", headers=_AUTH)
    assert resp.status_code == 400


def test_learning_snapshots_and_facts_routes(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    snaps = _client(db).get("/api/repos/r/learning/snapshots", headers=_AUTH).json()
    assert snaps["items"][0]["promotion_reason"] == "benefit_promoted"
    facts = _client(db).get("/api/repos/r/learning/facts", headers=_AUTH).json()
    assert facts["items"] == []  # none seeded, but route is live + fail-soft


def test_learning_routes_delegate_to_the_shared_observatory_controller(monkeypatch, tmp_path) -> None:
    """M3: preserve the established envelope while taking the learning reads through one controller."""
    db, _ = _seed_rich(tmp_path)
    import pebra.dashboard.api as api

    calls: list[tuple] = []

    def snapshots(repo_id, limit, *, port):
        calls.append(("snapshots", repo_id, limit, type(port).__name__))
        return [{"snapshot_id": "rs_controller"}]

    def facts(repo_id, snapshot_id, limit, *, port):
        calls.append(("facts", repo_id, snapshot_id, limit, type(port).__name__))
        return [{"fact_id": "lrf_controller"}]

    monkeypatch.setattr(api.oqc, "learning_snapshots", snapshots)
    monkeypatch.setattr(api.oqc, "learning_facts", facts)
    client = _client(db)

    assert client.get("/api/repos/r/learning/snapshots?limit=3", headers=_AUTH).json() == {
        "items": [{"snapshot_id": "rs_controller"}]
    }
    assert client.get("/api/repos/r/learning/facts?snapshot_id=rs_2&limit=4", headers=_AUTH).json() == {
        "items": [{"fact_id": "lrf_controller"}]
    }
    assert calls == [
        ("snapshots", "r", 3, "SqliteStore"),
        ("facts", "r", "rs_2", 4, "SqliteStore"),
    ]


class _StubReader:
    def __init__(self) -> None:
        self.calls: list = []

    def hot_subgraph(self, symbols, repo_root, *, max_depth=2, max_nodes=300):
        self.calls.append((symbols, repo_root, max_depth, max_nodes))
        return {"available": True, "nodes": [{"id": "n1"}], "edges": [], "graph_freshness": "fresh"}

    def file_overview(self, repo_root, *, top_n=200):
        return {"available": True, "files": [{"file_path": "src/Gamma.cs"}]}


def test_graph_hotspot_passes_resolved_names_and_repo_root(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    stub = _StubReader()
    app.state.graph_reader = stub
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.get(f"/api/repos/r/graph/hotspot?assessment_id={asm}", headers=_AUTH).json()

    assert body["available"] is True and body["nodes"] == [{"id": "n1"}]
    assert stub.calls == [
        (
            [
                {"qualified_name": "Gamma::Gamma", "file_path": None},
                {"qualified_name": "Gamma::LogGamma", "file_path": None},
            ],
            "/repo",
            2,
            300,
        )
    ]


def test_graph_hotspot_rejects_cross_repo_assessment(tmp_path) -> None:
    # The assessment belongs to repo "r"; requesting it under a different repo_id must 404, not leak
    # that assessment's resolved symbols into another repo's graph view (cross-repo IDOR).
    db, asm = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    resp = client.get(f"/api/repos/OTHER/graph/hotspot?assessment_id={asm}", headers=_AUTH)
    assert resp.status_code == 404


def test_graph_hotspot_rejects_url_repo_that_does_not_match_launched_repo(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    resp = client.get(f"/api/repos/OTHER/graph/hotspot?assessment_id={asm}", headers=_AUTH)
    assert resp.status_code == 404


def test_graph_hotspot_failsoft_without_repo_root(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    resp = _client(db).get(f"/api/repos/r/graph/hotspot?assessment_id={asm}", headers=_AUTH)
    assert resp.status_code == 200  # never 500
    assert resp.json()["available"] is False


def test_graph_overview_failsoft_without_repo_root(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    resp = _client(db).get("/api/repos/r/graph/overview", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_graph_overview_rejects_url_repo_that_does_not_match_launched_repo(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/repos/OTHER/graph/overview", headers=_AUTH).status_code == 404


def test_graph_overview_accepts_launched_repo(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.get("/api/repos/r/graph/overview", headers=_AUTH).json()
    assert body["available"] is True
    assert body["files"] == [{"file_path": "src/Gamma.cs"}]
