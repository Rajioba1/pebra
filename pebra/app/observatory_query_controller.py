"""observatory_query_controller (Observatory TUI M1) — the one place the read projections and
repo-scoping for assessment history live.

The FastAPI dashboard and the Textual TUI both read history through these functions, so the two surfaces
cannot drift on what a row/detail looks like or on the cross-repo boundary. Each function takes an
ObservatoryReadPort (SqliteStore satisfies it structurally) and returns raw data; the calling surface
adds its own envelope (FastAPI wraps lists in ``{"items": ...}``; the TUI consumes the rows directly).
This layer holds no decision, sanction, or learning math — only shaping and scoping of stored reads.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from pebra.ports.observatory_read_port import ObservatoryReadPort

# The lean per-assessment projection for the risk/benefit-over-time chart. Owned here so both surfaces
# project the same fields (previously duplicated in dashboard/api.py).
_SERIES_KEYS = ("expected_loss", "benefit", "expected_utility", "rau", "edit_confidence")


class AssessmentNotFoundError(Exception):
    """The assessment does not exist, or is not visible under the requested repo.

    Both cases collapse to one error so a caller cannot distinguish "missing" from "belongs to another
    repo" (the cross-repo boundary must not leak existence). Surfaces map this to a 404 / not-found.
    """


def list_assessments(
    repo_id: str, limit: int = 50, offset: int = 0, *, port: ObservatoryReadPort
) -> list[dict[str, Any]]:
    """Newest-first summaries, including store-projected identity (pass-through, no reshaping)."""
    return port.list_assessments(repo_id, limit, offset)


def overview(repo_id: str, *, port: ObservatoryReadPort) -> dict[str, Any]:
    """Counts by decision and by terminal status (None -> "pending") plus the store-wide chain verdict."""
    total = 0
    by_decision: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    for row in port.assessment_facets(repo_id):
        total += 1
        by_decision[row["decision"]] += 1
        by_status[row["terminal_status"] or "pending"] += 1
    return {
        "total": total,
        "by_decision": dict(by_decision),
        "by_status": dict(by_status),
        "chain": port.chain_status(),
    }


def scores_series(
    repo_id: str, limit: int = 200, offset: int = 0, *, port: ObservatoryReadPort
) -> list[dict[str, Any]]:
    """Lean per-assessment score projection (the five series keys, missing -> None)."""
    return [
        {
            "assessment_id": r["assessment_id"],
            "decision": r["decision"],
            "assessed_commit": r["assessed_commit"],
            "terminal_status": r["terminal_status"],
            "scores": {k: (r["scores"] or {}).get(k) for k in _SERIES_KEYS},
        }
        for r in port.list_assessments(repo_id, limit, offset)
    ]


def assessment_detail(assessment_id: str, *, port: ObservatoryReadPort) -> dict[str, Any]:
    """Full detail for one assessment. Raises AssessmentNotFoundError when it does not exist."""
    try:
        return port.assessment_detail(assessment_id)
    except KeyError as exc:
        raise AssessmentNotFoundError(assessment_id) from exc


def assessment_detail_for_repo(
    assessment_id: str, repo_id: str, *, port: ObservatoryReadPort
) -> dict[str, Any]:
    """Detail scoped to a repo: raises AssessmentNotFoundError if it is missing OR belongs to another
    repo (assessment_detail is not repo-scoped, so the boundary is enforced here)."""
    detail = assessment_detail(assessment_id, port=port)
    if (detail.get("content") or {}).get("repo_id") != repo_id:
        raise AssessmentNotFoundError(assessment_id)
    return detail


def store_chain_status(*, port: ObservatoryReadPort) -> dict[str, Any]:
    """Store-wide audit-chain verdict + per-table row counts (database-global, not repo-scoped)."""
    return port.chain_status()


def learning_snapshots(
    repo_id: str, limit: int = 50, *, port: ObservatoryReadPort
) -> list[dict[str, Any]]:
    """Persisted learning snapshots; the surface owns its response envelope."""
    return port.list_risk_snapshots(repo_id, limit)


def learning_facts(
    repo_id: str,
    snapshot_id: str | None = None,
    limit: int = 200,
    *,
    port: ObservatoryReadPort,
) -> list[dict[str, Any]]:
    """Persisted learning facts; names mirror SnapshotFact without inventing a parallel schema."""
    return port.list_learned_risk_facts(repo_id, snapshot_id, limit)


def assessment_prior_facets(
    repo_id: str, assessment_ids: Sequence[str], *, port: ObservatoryReadPort
) -> dict[str, dict[str, Any]]:
    """Persisted, repo-scoped applied-prior summaries for the currently visible ledger rows."""
    return port.assessment_prior_facets(repo_id, assessment_ids)
