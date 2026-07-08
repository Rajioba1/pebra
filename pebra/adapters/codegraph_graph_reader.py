"""codegraph_graph_reader — read-only bulk node/edge reads for the Risk Observatory graph view.

The dashboard needs "give me a renderable subgraph" — a shape ``CodeGraphAdapter`` (per-symbol fan-in /
risk scoring) never had. Rather than grow that already-1400-line production-path adapter with
dashboard-only concerns, this sibling reuses its freshness/version/schema gates and edge-kind constants
by import and adds two read methods:

  * ``hot_subgraph`` — the blast radius around an assessment's changed symbols: BFS outward along the
    reverse (caller/dependent) edges the MODIFY-risk term already uses, bounded by depth + a hard node
    cap so a 13k-node graph never reaches the browser.
  * ``file_overview`` — the whole-repo view aggregated to the hottest files by inbound fan-in.

Boundaries: an ADAPTER (stdlib sqlite3 read-only + subprocess freshness gate via the shared status_fn;
imports only ``pebra.core`` + a sibling adapter). Fail-soft exactly like ``CodeGraphAdapter``: any gate
failure returns ``available=False`` with a reason and empty nodes/edges; it never raises.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from pebra.adapters.codegraph_adapter import (
    _CALLABLE_KINDS,
    _FANIN_EDGE_KINDS,
    _MIN_SCHEMA_VERSION,
    _MODIFY_IMPACT_EDGE_KINDS,
    CodeGraphAdapter,
    _db_path_from_status,
    _default_status,
    _is_fresh,
)
from pebra.core.graph_version import in_accepted_range


def _empty(available: bool, freshness: str, reason: str | None, **extra: Any) -> dict[str, Any]:
    base = {
        "available": available,
        "graph_freshness": freshness,
        "fallback_reason": reason,
        "nodes": [],
        "edges": [],
        "truncated": False,
        "total_node_count": 0,
    }
    base.update(extra)
    return base


class CodeGraphReader:
    """Read-only renderable-graph reads over codegraph's SQLite. ``status_fn`` is injectable (same as
    ``CodeGraphAdapter``) so the SQL is unit-testable without the binary."""

    def __init__(self, status_fn: Callable[[str], dict[str, Any] | None] | None = None) -> None:
        self._status_fn = status_fn or _default_status

    def _open(self, repo_root: str) -> tuple[sqlite3.Connection | None, str, str | None]:
        """Apply the same trust gates as ``CodeGraphAdapter.fanin`` and open the DB read-only.
        Returns ``(con, freshness, reason)`` — ``con is None`` (with a reason) on any gate failure."""
        status = self._status_fn(repo_root)
        if status is None:
            return None, "unknown", "codegraph CLI not found"
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return None, "unknown", f"codegraph version {runtime_ver} outside accepted range"
        if status.get("initialized") is False:
            return None, "unknown", "codegraph index not initialized"
        if not _is_fresh(status):
            return None, "stale", "codegraph index stale or worktree-mismatched"
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return None, "unknown", "codegraph DB not found"
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return None, "unknown", f"codegraph DB could not be opened: {exc}"
        con.row_factory = sqlite3.Row
        # The schema probe itself can throw on a corrupt / half-written / pre-schema DB (no
        # schema_versions table). Guard it so a bad DB fails soft (available=False) instead of raising
        # out of the un-try'd caller into a 500 — and never leak the connection.
        try:
            if CodeGraphAdapter._schema_version(con) < _MIN_SCHEMA_VERSION:
                con.close()
                return None, "fresh", f"codegraph schema below v{_MIN_SCHEMA_VERSION}"
        except (sqlite3.Error, OSError) as exc:
            con.close()
            return None, "unknown", f"codegraph DB query failed: {exc}"
        return con, "fresh", None

    @staticmethod
    def _resolve_ids(con: sqlite3.Connection, symbols: list[Any], max_nodes: int) -> tuple[list[str], bool, int]:
        """Map stored assessment symbols to graph node ids.

        ``symbols`` accepts the legacy list[str] shape and the newer
        ``{"qualified_name": ..., "file_path": ...}`` shape. When file paths are present, they are part
        of identity so duplicate qualified names in different files do not replay onto unrelated nodes.
        """
        ids: list[str] = []
        seen: set[str] = set()
        truncated = False
        resolved_total = 0
        for sym in symbols:
            if isinstance(sym, dict):
                qn = sym.get("qualified_name")
                file_path = sym.get("file_path")
            else:
                qn = sym
                file_path = None
            if not qn:
                continue
            candidates = [qn]
            if "." in qn:
                candidates.append(qn.replace(".", "::"))
            ph = ",".join("?" * len(candidates))
            params: tuple[Any, ...]
            if file_path:
                params = (*candidates, str(file_path).replace("\\", "/"))
                rows = con.execute(
                    f"SELECT DISTINCT id FROM nodes WHERE qualified_name IN ({ph}) "
                    "AND replace(file_path, '\\', '/') = ? ORDER BY id",
                    params,
                ).fetchall()
            else:
                params = tuple(candidates)
                rows = con.execute(
                    f"SELECT DISTINCT id FROM nodes WHERE qualified_name IN ({ph}) ORDER BY id",
                    params,
                ).fetchall()
            for row in rows:
                resolved_total += 1
                node_id = row["id"]
                if node_id in seen:
                    continue
                if len(ids) >= max_nodes:
                    truncated = True
                    continue
                seen.add(node_id)
                ids.append(node_id)
        return ids, truncated, resolved_total

    def hot_subgraph(
        self,
        qualified_names: list[Any],
        repo_root: str,
        *,
        max_depth: int = 2,
        max_nodes: int = 300,
    ) -> dict[str, Any]:
        """Blast-radius subgraph around ``qualified_names``: BFS outward along reverse impact edges
        (callers/dependents) to ``max_depth``, capped at ``max_nodes`` nodes. Fail-soft."""
        con, freshness, reason = self._open(repo_root)
        if con is None:
            return _empty(False, freshness, reason)
        try:
            max_nodes = max(1, max_nodes)
            centers, truncated, resolved_center_count = self._resolve_ids(con, qualified_names, max_nodes)
            if not centers:
                return _empty(
                    True, "fresh", "no matching symbols in the current graph (renamed or removed?)"
                )
            depth: dict[str, int] = {c: 0 for c in centers}
            frontier = list(centers)
            truncated = truncated or len(depth) >= max_nodes
            edge_ph = ",".join("?" * len(_MODIFY_IMPACT_EDGE_KINDS))
            for d in range(1, max_depth + 1):
                if not frontier or len(depth) >= max_nodes:
                    break
                fro_ph = ",".join("?" * len(frontier))
                remaining = max_nodes - len(depth)
                sources = con.execute(
                    f"SELECT DISTINCT source FROM edges WHERE target IN ({fro_ph}) "
                    f"AND kind IN ({edge_ph}) ORDER BY source LIMIT ?",
                    (*frontier, *_MODIFY_IMPACT_EDGE_KINDS, remaining + 1),
                ).fetchall()
                nxt: list[str] = []
                for row in sources:
                    src = row["source"]
                    if src in depth:
                        continue
                    if len(depth) >= max_nodes:
                        truncated = True
                        break
                    depth[src] = d
                    nxt.append(src)
                frontier = nxt
            node_ids = list(depth)
            id_ph = ",".join("?" * len(node_ids))
            node_rows = con.execute(
                f"SELECT id, kind, qualified_name, file_path FROM nodes WHERE id IN ({id_ph})",
                tuple(node_ids),
            ).fetchall()
            nodes = [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "qualified_name": r["qualified_name"],
                    "file_path": (r["file_path"] or "").replace("\\", "/") or None,
                    "depth": depth[r["id"]],
                }
                for r in node_rows
            ]
            max_edges = max_nodes * 4
            edge_rows = con.execute(
                f"SELECT source, target, kind FROM edges WHERE source IN ({id_ph}) "
                f"AND target IN ({id_ph}) AND kind IN ({edge_ph}) ORDER BY source, target LIMIT ?",
                (*node_ids, *node_ids, *_MODIFY_IMPACT_EDGE_KINDS, max_edges + 1),
            ).fetchall()
            if len(edge_rows) > max_edges:
                truncated = True
                edge_rows = edge_rows[:max_edges]
            edges = [
                {"source": r["source"], "target": r["target"], "kind": r["kind"]}
                for r in edge_rows
            ]
            return {
                "available": True,
                "graph_freshness": "fresh",
                "fallback_reason": None,
                "nodes": nodes,
                "edges": edges,
                "truncated": truncated,
                "total_node_count": len(nodes),
                "center_count": resolved_center_count,
            }
        except (sqlite3.Error, OSError) as exc:
            return _empty(False, "unknown", f"codegraph DB query failed: {exc}")
        finally:
            con.close()

    def file_overview(self, repo_root: str, *, top_n: int = 200) -> dict[str, Any]:
        """Whole-repo view aggregated to the hottest files by distinct inbound fan-in. Files with zero
        fan-in are omitted (nothing to show). Capped at ``top_n`` (``truncated`` if more exist)."""
        con, freshness, reason = self._open(repo_root)
        if con is None:
            return {
                "available": False, "graph_freshness": freshness, "fallback_reason": reason,
                "files": [], "truncated": False, "total_file_count": 0,
            }
        try:
            call_ph = ",".join("?" * len(_CALLABLE_KINDS))
            edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
            rows = con.execute(
                f"SELECT n.file_path AS f, COUNT(DISTINCT e.source) AS callers "
                f"FROM edges e JOIN nodes n ON n.id = e.target "
                f"WHERE n.kind IN ({call_ph}) AND e.kind IN ({edge_ph}) AND n.file_path IS NOT NULL "
                f"GROUP BY n.file_path HAVING callers > 0 ORDER BY callers DESC, n.file_path ASC",
                (*_CALLABLE_KINDS, *_FANIN_EDGE_KINDS),
            ).fetchall()
            total = len(rows)
            capped = rows[: max(0, top_n)]
            files = [
                {"file_path": (r["f"] or "").replace("\\", "/"), "distinct_caller_count": int(r["callers"])}
                for r in capped
            ]
            return {
                "available": True,
                "graph_freshness": "fresh",
                "fallback_reason": None,
                "files": files,
                "truncated": total > len(files),
                "total_file_count": total,
            }
        except (sqlite3.Error, OSError) as exc:
            return {
                "available": False, "graph_freshness": "unknown",
                "fallback_reason": f"codegraph DB query failed: {exc}",
                "files": [], "truncated": False, "total_file_count": 0,
            }
        finally:
            con.close()
