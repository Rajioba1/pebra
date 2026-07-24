"""Risk Observatory read API (Phase 3b/5c-C, extended Phase 5d). Bearer-guarded, read-only JSON.

The dashboard surface reads through the shared Observatory query controller (app) and opens a SqliteStore
per request (own connection in the request's thread) and closes it; it may import adapters + core and the
read-only query controller, but never a mutation controller (see .importlinter). Graph routes additionally
use a CodeGraphReader + the repo_root the dashboard was launched against, both from app.state; they are
fail-soft (200 with ``available:false`` when the graph or repo binding is missing — never 500).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from pebra.adapters.store.db import SqliteStore
from pebra.app import observatory_query_controller as oqc
from pebra.core.dashboard_metrics import reliability_bins

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
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return {"items": oqc.list_assessments(repo_id, limit, offset, port=store)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/overview")
    def overview(repo_id: str, request: Request) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return oqc.overview(repo_id, port=store)
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
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return {"items": oqc.scores_series(repo_id, limit, offset, port=store)}
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
        _require_bound_repo(request, repo_id)
        if not getattr(request.app.state, "dev_mode", False):
            raise HTTPException(status_code=404, detail="not found")
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
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return {"items": oqc.learning_snapshots(repo_id, limit, port=store)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/learning/facts")
    def learning_facts(
        repo_id: str,
        request: Request,
        snapshot_id: str | None = Query(None),
        limit: int = Query(200, ge=0, le=1000),
    ) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return {"items": oqc.learning_facts(repo_id, snapshot_id, limit, port=store)}
        finally:
            store.close()

    @router.get("/repos/{repo_id}/learning/context")
    def learning_context(
        repo_id: str, request: Request, limit: int = Query(200, ge=0, le=1000)
    ) -> dict[str, Any]:
        """Verified outcome lessons, repo-scoped and shaped by the shared read controller."""
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return oqc.learning_context(repo_id, limit=limit, port=store)
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
        try:
            payload = request.app.state.graph_reader.hot_subgraph(
                symbols, repo_root, max_depth=max_depth, max_nodes=max_nodes
            )
        except Exception:  # noqa: BLE001 — hard rule: a graph read must fail soft, never 500
            return _graph_unavailable("codegraph graph data unavailable")
        return _with_graph_setup_hint(payload)

    @router.get("/repos/{repo_id}/graph/overview")
    def graph_overview(
        repo_id: str, request: Request, top_n: int = Query(200, ge=1, le=1000)
    ) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        repo_root = getattr(request.app.state, "repo_root", None)
        if not repo_root:
            return _graph_overview_unavailable("dashboard is not bound to a repo root")
        try:
            payload = request.app.state.graph_reader.file_overview(repo_root, top_n=top_n)
        except Exception:  # noqa: BLE001 — hard rule: a graph read must fail soft, never 500
            return _graph_overview_unavailable("codegraph graph data unavailable")
        return _with_graph_setup_hint(payload)

    @router.get("/repos/{repo_id}/graph/godmap")
    def graph_godmap(
        repo_id: str,
        request: Request,
        max_files: int = Query(20, ge=1, le=100),
        max_symbols_per_file: int = Query(10, ge=1, le=50),
        max_nodes: int = Query(250, ge=1, le=2000),
        max_edges: int = Query(800, ge=1, le=10000),
    ) -> dict[str, Any]:
        """Readable whole-repo graph: hot file hubs plus top symbols. No assessment lookup, so the URL
        repo boundary and bearer auth are the only access controls, same as ``graph_full``."""
        _require_bound_repo(request, repo_id)
        repo_root = getattr(request.app.state, "repo_root", None)
        if not repo_root:
            return _graph_godmap_unavailable("dashboard is not bound to a repo root")
        try:
            payload = request.app.state.graph_reader.god_node_map(
                repo_root,
                max_files=max_files,
                max_symbols_per_file=max_symbols_per_file,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        except Exception:  # noqa: BLE001 — hard rule: a graph read must fail soft, never 500
            return _graph_godmap_unavailable("codegraph graph data unavailable")
        return _with_graph_setup_hint(payload)

    @router.get("/repos/{repo_id}/graph/full")
    def graph_full(
        repo_id: str,
        request: Request,
        max_nodes: int = Query(250, ge=1, le=20000),
        max_edges: int = Query(40000, ge=1, le=100000),
        collapse_after: int = Query(300, ge=1, le=20000),
    ) -> dict[str, Any]:
        """Whole-repo structural graph (symbol or file-collapsed), read-only and fail-soft. No
        assessment lookup, so there is no cross-repo assessment surface — the URL repo boundary and
        bearer auth are the only access controls, same as ``graph_overview``."""
        _require_bound_repo(request, repo_id)
        repo_root = getattr(request.app.state, "repo_root", None)
        if not repo_root:
            return _graph_full_unavailable("dashboard is not bound to a repo root")
        try:
            payload = request.app.state.graph_reader.full_graph(
                repo_root, max_nodes=max_nodes, max_edges=max_edges, collapse_after=collapse_after
            )
        except Exception:  # noqa: BLE001 — hard rule: a graph read must fail soft, never 500
            return _graph_full_unavailable("codegraph graph data unavailable")
        return _with_graph_setup_hint(payload)

    @router.get("/repos/{repo_id}/assessments/{assessment_id}")
    def repo_detail(repo_id: str, assessment_id: str, request: Request) -> dict[str, Any]:
        _require_bound_repo(request, repo_id)
        store = _open(request)
        try:
            return oqc.assessment_detail_for_repo(assessment_id, repo_id, port=store)
        except oqc.AssessmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="assessment not found") from exc
        finally:
            store.close()

    @router.get("/assessments/{assessment_id}")
    def detail(assessment_id: str, request: Request) -> dict[str, Any]:
        bound_repo = getattr(request.app.state, "repo_id", None)
        store = _open(request)
        try:
            if bound_repo is not None:
                return oqc.assessment_detail_for_repo(assessment_id, bound_repo, port=store)
            return oqc.assessment_detail(assessment_id, port=store)
        except oqc.AssessmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="assessment not found") from exc
        finally:
            store.close()

    @router.get("/chain-status")
    def chain_status(request: Request) -> dict[str, Any]:
        store = _open(request)
        try:
            return oqc.store_chain_status(port=store)
        finally:
            store.close()

    return router


def _graph_unavailable(reason: str) -> dict[str, Any]:
    return _with_graph_setup_hint({
        "available": False, "graph_freshness": "unknown", "fallback_reason": reason,
        "nodes": [], "edges": [], "truncated": False, "total_node_count": 0,
    })


def _graph_overview_unavailable(reason: str) -> dict[str, Any]:
    return _with_graph_setup_hint({
        "available": False, "graph_freshness": "unknown",
        "fallback_reason": reason,
        "files": [], "truncated": False, "total_file_count": 0,
    })


def _graph_full_unavailable(reason: str) -> dict[str, Any]:
    return _with_graph_setup_hint({
        "available": False, "graph_freshness": "unknown", "fallback_reason": reason,
        "mode": "symbol", "collapsed": False,
        "nodes": [], "edges": [], "truncated": False,
        "total_node_count": 0, "total_edge_count": 0,
    })


def _graph_godmap_unavailable(reason: str) -> dict[str, Any]:
    return _with_graph_setup_hint({
        "available": False, "graph_freshness": "unknown", "fallback_reason": reason,
        "mode": "godmap", "collapsed": False,
        "nodes": [], "edges": [], "truncated": False,
        "total_file_count": 0, "total_symbol_count": 0,
        "total_node_count": 0, "total_edge_count": 0,
    })


def _with_graph_setup_hint(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("available") is False:
        fallback_reason = _public_graph_fallback_reason(payload.get("fallback_reason"))
        payload["fallback_reason"] = fallback_reason
        if fallback_reason.startswith("dashboard is not bound to a repo root"):
            payload["setup_command"] = "pebra dashboard --repo-root <path>"
            payload["setup_hint"] = (
                "Relaunch the dashboard with the repository root so graph reads can be scoped."
            )
        else:
            payload["setup_command"] = "pebra setup-graph --fix --repo-root ."
            payload["setup_hint"] = "Initialize or repair the local CodeGraph index, then refresh this tab."
    return payload


def _public_graph_fallback_reason(reason: object) -> str:
    text = reason if isinstance(reason, str) else ""
    safe_prefixes = (
        "dashboard is not bound to a repo root",
        "codegraph CLI not found",
        "codegraph version ",
        "codegraph index not initialized",
        "codegraph index stale or worktree-mismatched",
        "codegraph DB not found",
        "codegraph schema below v",
        "no matching symbols in the current graph",
    )
    if any(text.startswith(prefix) for prefix in safe_prefixes):
        return text
    return "codegraph graph data unavailable"


def _require_bound_repo(request: Request, repo_id: str) -> None:
    bound_repo = getattr(request.app.state, "repo_id", None)
    if bound_repo is not None and bound_repo != repo_id:
        raise HTTPException(status_code=404, detail="repo not found")
