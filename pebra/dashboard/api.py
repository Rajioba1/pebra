"""Risk Observatory read API (Phase 3b/5c-C). Bearer-guarded, read-only JSON over the SQLite store.

The dashboard surface reads the store directly (it may import adapters; never app/core). Routes open a
SqliteStore per request (own connection in the request's thread) and close it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from pebra.adapters.store.db import SqliteStore


def build_router(require_bearer: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_bearer)])

    def _open(request: Request) -> SqliteStore:
        return SqliteStore(request.app.state.db_path)

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

    @router.get("/assessments/{assessment_id}")
    def detail(assessment_id: str, request: Request) -> dict[str, Any]:
        store = _open(request)
        try:
            return store.assessment_detail(assessment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="assessment not found") from exc
        finally:
            store.close()

    @router.get("/chain-status")
    def chain_status(request: Request) -> dict[str, Any]:
        store = _open(request)
        try:
            return store.chain_status()
        finally:
            store.close()

    return router
