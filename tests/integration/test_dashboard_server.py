"""Phase 3b/5c-C/D — Risk Observatory FastAPI app: bearer auth, Host allowlist, CSP, read routes.

Needs fastapi/jinja2/httpx -> nox only.
"""

from __future__ import annotations

from dataclasses import replace
import importlib.util

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.learning_context import entry_hash
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


def test_index_vendors_cytoscape_without_cdn(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/")
    assert resp.status_code == 200
    body = resp.text
    # Cytoscape is self-hosted under /static/vendor and nonce-tagged like uPlot.
    assert '/static/vendor/cytoscape.min.js' in body
    csp = resp.headers["content-security-policy"]
    nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}" src="/static/vendor/cytoscape.min.js">' in body
    # No CDN/runtime network dependency for the graph library.
    for host in ("unpkg.com", "jsdelivr", "cdnjs", "http://", "https://"):
        assert host not in body


def test_app_js_renders_full_graph_with_cytoscape_webgl(tmp_path) -> None:
    # Structural backstop for the graph rewrite (behavioural proof is the Playwright smoke in
    # tests/ui_e2e/test_graph_tab_e2e.py). Confirms the served app.js wires the full-graph route to
    # Cytoscape's WebGL renderer, dropped the old hand-rolled canvas, and never uses innerHTML.
    db, _ = _seed(tmp_path)
    js = _client(db).get("/static/app.js").text
    assert js
    assert "/graph/full" in js
    assert "cytoscape(" in js
    assert "webgl: true" in js
    assert "drawGraph(" not in js       # old radial canvas renderer removed
    assert "#graph-canvas" not in js
    assert "innerHTML" not in js        # user/repo-derived text rendered via textContent only


def test_app_js_wires_graph_search_inspector_and_layouts(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    js = _client(db).get("/static/app.js").text
    assert "graphSearch(" in js
    assert "search-hit" in js and "search-dim" in js
    assert "showInspector(" in js
    assert "runGraphLayout(" in js
    assert "concentric" in js and "circle" in js  # layout options
    assert "graph-inspector" in js
    assert 'setAttribute("tabindex", "0")' in js   # inspector is the keyboard-reachable a11y fallback


def test_app_js_wires_risk_overlay_honestly(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    js = _client(db).get("/static/app.js").text
    assert "setOverlay(" in js and "applyOverlay(" in js and "loadAssessmentRisk(" in js
    assert "DECISION_STYLE" in js and "rb-unmatched" in js
    # Honest framing: decision drives colour; loss stays loss points; the aggregate caveat is shown.
    assert "assessment aggregate. This is not per-symbol calibrated risk" in js
    assert "loss pts" in js
    # decision (categorical) is the only node-colour signal in risk view.
    assert "RISK_DECISIONS" in js and 'node.rb-' in js


def test_index_hides_calibration_tab_by_default(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/")

    assert resp.status_code == 200
    assert 'data-tab="calibration"' not in resp.text


def test_index_shows_calibration_tab_in_dev_mode(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    db, _ = _seed(tmp_path)
    resp = TestClient(
        create_app(db, "tok", dev_mode=True), base_url="http://127.0.0.1"
    ).get("/")

    assert resp.status_code == 200
    assert 'data-tab="calibration"' in resp.text


def test_api_requires_bearer(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    assert _client(db).get("/api/chain-status").status_code == 401


def test_api_with_bearer_returns_chain_status(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    resp = _client(db).get("/api/chain-status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_calibration_route_accepts_review_cost_target(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    db, _ = _seed(tmp_path)
    client = TestClient(create_app(db, "tok", dev_mode=True), base_url="http://127.0.0.1")
    resp = client.get(
        "/api/repos/r/calibration?target_type=cost_continuous&scope=all", headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["target_type"] == "cost_continuous"


def test_calibration_route_is_dev_only(tmp_path) -> None:
    db, _ = _seed(tmp_path)

    resp = _client(db).get("/api/repos/r/calibration?target_type=risk_binary", headers=_AUTH)

    assert resp.status_code == 404


def test_dashboard_calibration_control_lists_review_cost(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert '["cost_continuous", "review cost (continuous)"]' in text


def test_dashboard_learning_projection_includes_verified_lessons(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    assert 'getJSON(rp("/learning/context?limit=200"))' in text
    assert 'card("Verified lessons")' in text
    assert "No verified completed outcomes" in text
    assert 'headRow(["record", "assessment", "task", "lesson", "verified outcome", "created"])' in text
    assert "cell(item.verification_summary" in text


def test_dashboard_history_numeric_headers_are_aligned_with_numeric_cells(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    css = _client(db).get("/static/style.css").text

    assert '{ label: "expected loss", cls: "num" }' in text
    assert '{ label: "benefit", cls: "num" }' in text
    assert '{ label: "expected utility", cls: "num" }' in text
    assert '{ label: "rau", cls: "num" }' in text
    assert '{ label: "confidence", cls: "num" }' in text
    assert "th.num" in css


def test_dashboard_history_labels_terminal_status_as_outcome(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text

    assert '"outcome"' in text
    assert '{ label: "confidence", cls: "num" },\n        "status",' not in text


def test_dashboard_history_renders_expected_loss_as_points_and_benefit_as_percentage(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text

    assert '{ label: "expected loss", cls: "num" }' in text
    assert "cell(fmtLossPoints(s.expected_loss), \"num\")" in text
    assert "cell(fmtPct(s.benefit), \"num\")" in text
    assert "cell(fmt(s.rau), \"num\")" in text


def test_dashboard_history_includes_identity_and_lesson_columns(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text

    assert 'getJSON(rp("/learning/context?limit=200"))' in text
    assert '"task", "target", "fingerprint"' in text
    assert "formatTask(it.task)" in text
    assert "formatTarget(it.target_files)" in text
    assert "formatFingerprint(it.candidate_fingerprint)" in text
    assert "lessonIndicator(lessonByAssessment[it.assessment_id], lessons.status)" in text


def test_dashboard_history_wide_table_scrolls_horizontally(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text
    css = _client(db).get("/static/style.css").text

    assert 'el("div", "table-scroll")' in text
    assert ".table-scroll" in css
    assert "overflow-x: auto" in css


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


def test_learning_context_route_serves_verified_lessons(tmp_path) -> None:
    """Milestone 0 forward spec for Milestone 5C: a repo-scoped, bearer-guarded /learning/context
    route exposes verified lessons through the same {'items': [...]} envelope."""
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"expected_loss": 0.1, "benefit": 0.82, "rau": 0.31},
            repo_id="r", repo_root="/x", assessed_commit="abc123",
        ),
        {
            "task": "Fix [login]", "action_id": "edit-auth",
            "revision_envelope": {"expected_files": ["src/auth.py"]},
        },
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    assert store.materialize_learning_context(assessment_id) is not None
    store.close()
    client = _client(db)
    assert client.get("/api/repos/r/learning/context").status_code == 401
    resp = client.get("/api/repos/r/learning/context", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "available"
    assert resp.json()["items"][0]["lesson"] == (
        "Verified completed outcome for Fix [login]; PEBRA decision was proceed."
    )


def test_learning_context_route_degrades_tampered_history(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL, scores={},
            repo_id="r", repo_root="/x",
        ),
        {"task": "Fix login", "action_id": "a1"},
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    assert store.materialize_learning_context(assessment_id) is not None
    store._con.execute("UPDATE learning_context SET lesson = 'tampered'")
    store._con.commit()
    store.close()

    assert _client(db).get(
        "/api/repos/r/learning/context", headers=_AUTH
    ).json() == {"status": "unavailable", "items": []}


@pytest.mark.parametrize(
    "table",
    ["assessments", "outcomes", "post_assessment_guardrails", "learning_context"],
)
def test_learning_context_route_requires_every_trusted_source_chain(tmp_path, table) -> None:
    db = str(tmp_path / f"{table}.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL, scores={},
            repo_id="r", repo_root="/x",
        ),
        {"task": "Fix login", "action_id": "a1"},
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    assert store.materialize_learning_context(assessment_id) is not None
    store._con.execute(f"UPDATE {table} SET row_hash = ?", ("0" * 64,))
    store._con.commit()
    store.close()

    response = _client(db).get("/api/repos/r/learning/context", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "items": []}


def test_learning_context_route_requires_a_current_proceed_guardrail(tmp_path) -> None:
    db = str(tmp_path / "guardrail-deleted.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL, scores={},
            repo_id="r", repo_root="/x",
        ),
        {"task": "Fix login", "action_id": "a1"},
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    assert store.materialize_learning_context(assessment_id) is not None
    store._con.execute("DELETE FROM post_assessment_guardrails")
    store._con.commit()
    store.close()

    response = _client(db).get("/api/repos/r/learning/context", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "items": []}


def test_learning_context_route_rejects_a_valid_chain_with_a_wrong_source_link(tmp_path) -> None:
    db = str(tmp_path / "wrong-source.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL, scores={},
            repo_id="r", repo_root="/x",
        ),
        {"task": "Fix login", "action_id": "a1"},
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    entry = store.materialize_learning_context(assessment_id)
    assert entry is not None
    previous_hash = store._con.execute(
        "SELECT previous_hash FROM learning_context WHERE assessment_id = ?",
        (assessment_id,),
    ).fetchone()[0]
    forged = replace(entry, source_outcome_hash="f" * 64)
    store._con.execute(
        "UPDATE learning_context SET source_outcome_hash = ?, row_hash = ? "
        "WHERE assessment_id = ?",
        (forged.source_outcome_hash, entry_hash(forged, previous_hash), assessment_id),
    )
    store._con.commit()
    assert store._validate_learning_context_chain() is False
    store.close()

    response = _client(db).get("/api/repos/r/learning/context", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "items": []}


def test_learning_context_route_is_empty_for_readonly_legacy_store(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    store = SqliteStore(db)
    store._con.execute("DROP TABLE learning_context_fts")
    store._con.execute("DROP TABLE learning_context")
    store._con.commit()
    store.close()

    from fastapi.testclient import TestClient
    from pebra.dashboard.server import create_app

    client = TestClient(
        create_app(db, "tok", repo_id="r", read_only=True),
        base_url="http://127.0.0.1",
    )
    response = client.get("/api/repos/r/learning/context", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "empty", "items": []}


def test_learning_context_route_is_unavailable_for_readonly_pre_gates_schema(tmp_path) -> None:
    db = str(tmp_path / "legacy.db")
    store = SqliteStore(db)
    assessment_id = store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL, scores={},
            repo_id="r", repo_root="/x",
        ),
        {"task": "Fix legacy login", "action_id": "legacy-login"},
    )
    store.persist_guardrails(assessment_id, {"pre_commit_decision": "proceed"})
    store.record_outcome(assessment_id, "completed", {})
    assert store.materialize_learning_context(assessment_id) is not None
    assert store._con.execute("SELECT COUNT(*) FROM learning_context").fetchone()[0] == 1
    store._con.execute("ALTER TABLE learning_context DROP COLUMN gates_fired")
    store._con.commit()
    store.close()

    from fastapi.testclient import TestClient
    from pebra.dashboard.server import create_app

    client = TestClient(
        create_app(db, "tok", repo_id="r", read_only=True),
        base_url="http://127.0.0.1",
    )
    response = client.get("/api/repos/r/learning/context", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "items": []}


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
    "/api/repos/other/learning/context",
)
_USER_REPO_COLLECTION_PATHS = tuple(
    path for path in _REPO_COLLECTION_PATHS if not path.endswith("/calibration")
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


@pytest.mark.parametrize("path", _USER_REPO_COLLECTION_PATHS)
def test_unbound_dashboard_preserves_multi_repo_collection_access(tmp_path, path: str) -> None:
    db, _ = _seed(tmp_path)
    store = SqliteStore(db)
    foreign = _persist_assessment(store, repo_id="other")
    store.close()

    response = _client(db).get(path, headers=_AUTH)

    assert response.status_code == 200
    if path.endswith("/assessments"):
        assert response.json()["items"][0]["assessment_id"] == foreign


def test_unbound_dev_dashboard_preserves_calibration_multi_repo_access(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    db, _ = _seed(tmp_path)
    store = SqliteStore(db)
    _persist_assessment(store, repo_id="other")
    store.close()
    client = TestClient(create_app(db, "tok", dev_mode=True), base_url="http://127.0.0.1")

    response = client.get("/api/repos/other/calibration", headers=_AUTH)

    assert response.status_code == 200


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
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    db, _ = _seed_rich(tmp_path)
    client = TestClient(create_app(db, "tok", dev_mode=True), base_url="http://127.0.0.1")
    body = client.get("/api/repos/r/calibration?target_type=risk_binary", headers=_AUTH).json()
    assert body["target_type"] == "risk_binary"
    assert len(body["bins"]) == 10
    assert body["sample_count"] == 2


def test_calibration_unknown_target_type_is_400(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from pebra.dashboard.server import create_app

    db, _ = _seed_rich(tmp_path)
    client = TestClient(create_app(db, "tok", dev_mode=True), base_url="http://127.0.0.1")
    resp = client.get("/api/repos/r/calibration?target_type=bogus", headers=_AUTH)
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

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        self.calls.append(("full", repo_root, max_nodes, max_edges, collapse_after))
        return {
            "available": True, "mode": "symbol", "collapsed": False,
            "graph_freshness": "fresh", "fallback_reason": None,
            "nodes": [{"id": "n1", "kind": "function"}], "edges": [],
            "truncated": False, "total_node_count": 1, "total_edge_count": 0,
        }


class _UnavailableReader:
    def hot_subgraph(self, symbols, repo_root, *, max_depth=2, max_nodes=300):
        return {
            "available": False,
            "graph_freshness": "unknown",
            "fallback_reason": "codegraph DB could not be opened: C:\\Users\\Raj\\secret\\codegraph.db",
            "nodes": [],
            "edges": [],
            "truncated": False,
            "total_node_count": 0,
        }

    def file_overview(self, repo_root, *, top_n=200):
        return {
            "available": False,
            "graph_freshness": "unknown",
            "fallback_reason": "codegraph DB query failed: /home/raj/secret/codegraph.db",
            "files": [],
            "truncated": False,
            "total_file_count": 0,
        }

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        return {
            "available": False,
            "graph_freshness": "unknown",
            "fallback_reason": "codegraph DB query failed: /home/raj/secret/codegraph.db",
            "mode": "symbol", "collapsed": False,
            "nodes": [], "edges": [],
            "truncated": False, "total_node_count": 0, "total_edge_count": 0,
        }


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
    assert "setup_command" not in body
    assert "setup_hint" not in body
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
    assert resp.json()["setup_command"] == "pebra dashboard --repo-root <path>"
    assert "relaunch" in resp.json()["setup_hint"].lower()


def test_graph_hotspot_sanitizes_reader_unavailable_reason(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _UnavailableReader()
    client = TestClient(app, base_url="http://127.0.0.1")

    body = client.get(f"/api/repos/r/graph/hotspot?assessment_id={asm}", headers=_AUTH).json()

    assert body["available"] is False
    assert body["fallback_reason"] == "codegraph graph data unavailable"
    assert body["setup_command"] == "pebra setup-graph --fix --repo-root ."
    assert "Users" not in str(body)
    assert "secret" not in str(body)


def test_graph_overview_failsoft_without_repo_root(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    resp = _client(db).get("/api/repos/r/graph/overview", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert resp.json()["setup_command"] == "pebra dashboard --repo-root <path>"
    assert "relaunch" in resp.json()["setup_hint"].lower()


def test_graph_overview_sanitizes_reader_unavailable_reason(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _UnavailableReader()
    client = TestClient(app, base_url="http://127.0.0.1")

    body = client.get("/api/repos/r/graph/overview", headers=_AUTH).json()

    assert body["available"] is False
    assert body["fallback_reason"] == "codegraph graph data unavailable"
    assert body["setup_command"] == "pebra setup-graph --fix --repo-root ."
    assert "home" not in str(body)


# ---- /graph/full (M3) --------------------------------------------------------


def test_graph_full_returns_reader_payload(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    stub = _StubReader()
    app.state.graph_reader = stub
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.get("/api/repos/r/graph/full", headers=_AUTH).json()

    assert body["available"] is True
    assert body["mode"] == "symbol" and body["collapsed"] is False
    assert body["nodes"] == [{"id": "n1", "kind": "function"}]
    assert "setup_command" not in body
    assert stub.calls == [("full", "/repo", 8000, 40000, 20000)]


def test_graph_full_clamps_query_bounds(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    # ceilings: nodes<=20000, edges<=100000, collapse_after<=20000; floors >=1
    assert client.get("/api/repos/r/graph/full?max_nodes=20001", headers=_AUTH).status_code == 422
    assert client.get("/api/repos/r/graph/full?max_edges=100001", headers=_AUTH).status_code == 422
    assert client.get("/api/repos/r/graph/full?collapse_after=0", headers=_AUTH).status_code == 422


def test_graph_full_forwards_bounds_to_reader(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    stub = _StubReader()
    app.state.graph_reader = stub
    client = TestClient(app, base_url="http://127.0.0.1")
    client.get(
        "/api/repos/r/graph/full?max_nodes=100&max_edges=200&collapse_after=50", headers=_AUTH
    )
    assert stub.calls == [("full", "/repo", 100, 200, 50)]


def test_graph_full_rejects_cross_repo(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _StubReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/repos/OTHER/graph/full", headers=_AUTH).status_code == 404


def test_graph_full_requires_bearer(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    resp = _client(db).get("/api/repos/r/graph/full")  # no auth header
    assert resp.status_code in (401, 403)


def test_graph_full_failsoft_without_repo_root(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    resp = _client(db).get("/api/repos/r/graph/full", headers=_AUTH)
    assert resp.status_code == 200  # never 500
    body = resp.json()
    assert body["available"] is False
    assert body["mode"] == "symbol" and body["collapsed"] is False
    assert body["nodes"] == [] and body["edges"] == []
    assert body["setup_command"] == "pebra dashboard --repo-root <path>"
    assert "relaunch" in body["setup_hint"].lower()


class _RaisingReader:
    """A reader whose graph methods raise a NON-(sqlite3/OSError) exception with a path in it."""

    def hot_subgraph(self, symbols, repo_root, *, max_depth=2, max_nodes=300):
        raise RuntimeError("unexpected /home/raj/secret boom")

    def file_overview(self, repo_root, *, top_n=200):
        raise RuntimeError("unexpected /home/raj/secret boom")

    def full_graph(self, repo_root, *, max_nodes=8000, max_edges=40000, collapse_after=20000):
        raise RuntimeError("unexpected /home/raj/secret boom")


def test_graph_full_failsoft_when_reader_raises(tmp_path) -> None:
    # Hard rule: no graph route may 500. If the reader raises something other than the sqlite/OS
    # errors it softens internally, the route must still fail soft (200, available=False), and must
    # not leak the exception text/path.
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _RaisingReader()
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    resp = client.get("/api/repos/r/graph/full", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["mode"] == "symbol" and body["nodes"] == [] and body["edges"] == []
    assert "secret" not in str(body) and "home" not in str(body)


def test_graph_overview_failsoft_when_reader_raises(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _RaisingReader()
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    resp = client.get("/api/repos/r/graph/overview", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert "secret" not in str(resp.json())


def test_graph_hotspot_failsoft_when_reader_raises(tmp_path) -> None:
    db, asm = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _RaisingReader()
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    resp = client.get(f"/api/repos/r/graph/hotspot?assessment_id={asm}", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert "secret" not in str(resp.json())


def test_graph_full_sanitizes_reader_unavailable_reason(tmp_path) -> None:
    db, _ = _seed_rich(tmp_path)
    from pebra.dashboard.server import create_app
    from fastapi.testclient import TestClient

    app = create_app(db, "tok", repo_id="r", repo_root="/repo")
    app.state.graph_reader = _UnavailableReader()
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.get("/api/repos/r/graph/full", headers=_AUTH).json()

    assert body["available"] is False
    assert body["fallback_reason"] == "codegraph graph data unavailable"
    assert body["setup_command"] == "pebra setup-graph --fix --repo-root ."
    assert "secret" not in str(body) and "home" not in str(body)
    assert "secret" not in str(body)


def test_dashboard_graph_fallback_renders_setup_guidance(tmp_path) -> None:
    db, _ = _seed(tmp_path)
    text = _client(db).get("/static/app.js").text

    assert "Graph setup:" in text
    assert "setup_command" in text
    assert "pebra setup-graph --fix" in text


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
    assert "setup_command" not in body
    assert "setup_hint" not in body
