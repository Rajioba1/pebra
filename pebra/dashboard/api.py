"""Risk Observatory read API (Phase 3b/5c-C, extended Phase 5d). Bearer-guarded, read-only JSON.

The dashboard surface reads the store directly (it may import adapters + core; never app). Routes open a
SqliteStore per request (own connection in the request's thread) and close it. Graph routes additionally
use a CodeGraphReader + the repo_root the dashboard was launched against, both from app.state; they are
fail-soft (200 with ``available:false`` when the graph or repo binding is missing — never 500).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from pebra.adapters.store.db import SqliteStore
from pebra.core.dashboard_metrics import reliability_bins

_SERIES_KEYS = ("expected_loss", "benefit", "expected_utility", "rau", "edit_confidence")
_BINARY_TARGETS = ("risk_binary", "benefit_binary")


def build_router(require_bearer: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_bearer)])

    def _open(request: Request) -> SqliteStore:
        try:
            return SqliteStore(request.app.state.db_path,
                               read_only=getattr(request.app.state, "read_only", False))
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="assessment store unavailable") from exc

    @router.get("/repos/{repo_id}/assessments")
    def assessments(
        repo_id: str,
        request: Request,
        limit: int = Query(50, ge=0, le=500),  # route-level guard; the store also clamps
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        store = _open(request)
        try:
            return {"items": store.list_assessments(repo_id, limit, offset)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/overview")
    def overview(repo_id: str, request: Request) -> dict[str, Any]:
        store = _open(request)
        try:
            rows = store.list_assessments(repo_id, limit=500)
            return {
                "total": len(rows),
                "by_decision": dict(Counter(r["decision"] for r in rows)),
                "by_status": dict(Counter((r["terminal_status"] or "pending") for r in rows)),
                "chain": store.chain_status(),
            }
        finally:
            store.close()

    @router.get("/repos/{repo_id}/scores-series")
    def scores_series(
        repo_id: str,
        request: Request,
        limit: int = Query(200, ge=0, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Lean per-assessment score projection for the risk/benefit-over-time chart."""
        store = _open(request)
        try:
            items = [
                {
                    "assessment_id": r["assessment_id"],
                    "decision": r["decision"],
                    "assessed_commit": r["assessed_commit"],
                    "terminal_status": r["terminal_status"],
                    "scores": {k: (r["scores"] or {}).get(k) for k in _SERIES_KEYS},
                }
                for r in store.list_assessments(repo_id, limit, offset)
            ]
            return {"items": items}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/calibration")
    def calibration(
        repo_id: str,
        request: Request,
        target_type: str = Query("risk_binary"),
        scope: str = Query("production"),
    ) -> dict[str, Any]:
        """Reliability diagram (binary targets) or a predicted-vs-actual scatter (continuous)."""
        store = _open(request)
        try:
            rows = store.list_prediction_errors(repo_id, target_type=target_type, scope=scope)
        except ValueError as exc:  # unknown target_type / scope is a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            store.close()
        if target_type in _BINARY_TARGETS:
            pairs = [
                (r["predicted_probability"], int(r["actual_outcome"]))
                for r in rows
                if r["predicted_probability"] is not None and r["actual_outcome"] is not None
            ]
            return {
                "target_type": target_type, "scope": scope,
                "bins": reliability_bins(pairs), "scatter": [], "sample_count": len(pairs),
            }
        scatter = [
            {"predicted": r["predicted_value"], "actual": r["actual_value"]}
            for r in rows
            if r["predicted_value"] is not None and r["actual_value"] is not None
        ][:500]
        return {
            "target_type": target_type, "scope": scope,
            "bins": [], "scatter": scatter, "sample_count": len(scatter),
        }

    @router.get("/repos/{repo_id}/learning/snapshots")
    def learning_snapshots(
        repo_id: str, request: Request, limit: int = Query(50, ge=0, le=500)
    ) -> dict[str, Any]:
        store = _open(request)
        try:
            return {"items": store.list_risk_snapshots(repo_id, limit)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/learning/facts")
    def learning_facts(
        repo_id: str,
        request: Request,
        snapshot_id: str | None = Query(None),
        limit: int = Query(200, ge=0, le=1000),
    ) -> dict[str, Any]:
        store = _open(request)
        try:
            return {"items": store.list_learned_risk_facts(repo_id, snapshot_id, limit)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/graph/hotspot")
    def graph_hotspot(
        repo_id: str,
        request: Request,
        assessment_id: str = Query(...),
        max_depth: int = Query(2, ge=1, le=4),
        max_nodes: int = Query(300, ge=1, le=2000),
    ) -> dict[str, Any]:
        """Blast-radius subgraph around an assessment's changed symbols. Fail-soft when the dashboard
        isn't bound to a repo root (e.g. a replayed db) or the graph is unavailable."""
        _require_bound_repo(request, repo_id)
        repo_root = getattr(request.app.state, "repo_root", None)
        if not repo_root:
            return _graph_unavailable("dashboard is not bound to a repo root")
        store = _open(request)
        try:
            detail = store.assessment_detail(assessment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="assessment not found") from exc
        finally:
            store.close()
        # assessment_detail is not repo-scoped; enforce the URL's repo boundary here so a valid bearer
        # can't pull a foreign repo's assessment (cross-repo IDOR) through the {repo_id} path.
        if (detail.get("content") or {}).get("repo_id") != repo_id:
            raise HTTPException(status_code=404, detail="assessment not found")
        sse = (detail.get("content") or {}).get("scores", {}).get("symbol_scope_evidence", {})
        fanin = sse.get("symbol_fanin") or {}
        qnames = list(fanin.get("resolved_qualified_names") or [])
        paths = list(fanin.get("resolved_file_paths") or [])
        symbols = [
            {
                "qualified_name": qn,
                "file_path": paths[i] if i < len(paths) else None,
            }
            for i, qn in enumerate(qnames)
        ]
        return request.app.state.graph_reader.hot_subgraph(
            symbols, repo_root, max_depth=max_depth, max_nodes=max_nodes
        )

    @router.get("/repos/{repo_id}/graph/overview")
    def graph_overview(
        repo_id: str, request: Request, top_n: int = Query(200, ge=1, le=1000)
    ) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        repo_root = getattr(request.app.state, "repo_root", None)
        if not repo_root:
            return {
                "available": False, "graph_freshness": "unknown",
                "fallback_reason": "dashboard is not bound to a repo root",
                "files": [], "truncated": False, "total_file_count": 0,
            }
        return request.app.state.graph_reader.file_overview(repo_root, top_n=top_n)

    @router.get("/repos/{repo_id}/assessments/{assessment_id}")
    def repo_detail(repo_id: str, assessment_id: str, request: Request) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        return _assessment_detail_for_repo(request, assessment_id, repo_id)

    @router.get("/assessments/{assessment_id}")
    def detail(assessment_id: str, request: Request) -> dict[str, Any]:
        bound_repo = getattr(request.app.state, "repo_id", None)
        if bound_repo is not None:
            return _assessment_detail_for_repo(request, assessment_id, bound_repo)
        return _assessment_detail(request, assessment_id)

    @router.get("/chain-status")
    def chain_status(request: Request) -> dict[str, Any]:
        store = _open(request)
        try:
            return store.chain_status()
        finally:
            store.close()

    return router


def _assessment_detail(request: Request, assessment_id: str) -> dict[str, Any]:
    try:
        store = SqliteStore(request.app.state.db_path,
                            read_only=getattr(request.app.state, "read_only", False))
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="assessment store unavailable") from exc
    try:
        return store.assessment_detail(assessment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="assessment not found") from exc
    finally:
        store.close()


def _assessment_detail_for_repo(request: Request, assessment_id: str, repo_id: str) -> dict[str, Any]:
    detail = _assessment_detail(request, assessment_id)
    if (detail.get("content") or {}).get("repo_id") != repo_id:
        raise HTTPException(status_code=404, detail="assessment not found")
    return detail


def _graph_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False, "graph_freshness": "unknown", "fallback_reason": reason,
        "nodes": [], "edges": [], "truncated": False, "total_node_count": 0,
    }


def _require_bound_repo(request: Request, repo_id: str) -> None:
    bound_repo = getattr(request.app.state, "repo_id", None)
    if bound_repo is not None and bound_repo != repo_id:
        raise HTTPException(status_code=404, detail="repo not found")
