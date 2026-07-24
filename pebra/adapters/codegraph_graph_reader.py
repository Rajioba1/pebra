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
    _OWNER_KINDS,
    CodeGraphAdapter,
    _db_path_from_status,
    _is_fresh,
)
from pebra.core.graph_version import in_accepted_range

_SETUP_GRAPH_HINT = "run: pebra setup-graph --fix"

# The whole-repo structural graph renders semantically meaningful owners (callables + containers),
# not every raw AST node, connected by the same edge vocabulary the MODIFY-risk term reasons over —
# so "full graph" and "risk overlay" never disagree about what a node or edge is.
_STRUCTURAL_NODE_KINDS = _OWNER_KINDS
_STRUCTURAL_EDGE_KINDS = _MODIFY_IMPACT_EDGE_KINDS
_DEFAULT_FULL_GRAPH_MAX_NODES = 250
_DEFAULT_FULL_GRAPH_COLLAPSE_AFTER = 300
_DEFAULT_GODMAP_MAX_FILES = 20
_DEFAULT_GODMAP_SYMBOLS_PER_FILE = 10
_DEFAULT_GODMAP_MAX_NODES = 250
_DEFAULT_GODMAP_MAX_EDGES = 800


def _short_label(qualified_name: str | None, name: str | None) -> str:
    """A compact display label: the last identifier segment (``A::B::c`` -> ``c``), else the name."""
    if qualified_name:
        for sep in ("::", "."):
            if sep in qualified_name:
                tail = qualified_name.rsplit(sep, 1)[-1]
                if tail:
                    return tail
        return qualified_name
    return name or ""


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
        self._status_fn = status_fn or (lambda _repo_root: None)

    def _open(self, repo_root: str) -> tuple[sqlite3.Connection | None, str, str | None]:
        """Apply the same trust gates as ``CodeGraphAdapter.fanin`` and open the DB read-only.
        Returns ``(con, freshness, reason)`` — ``con is None`` (with a reason) on any gate failure."""
        status = self._status_fn(repo_root)
        if status is None:
            return None, "unknown", f"codegraph CLI not found; {_SETUP_GRAPH_HINT}"
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return None, "unknown", (
                f"codegraph version {runtime_ver} outside accepted range; {_SETUP_GRAPH_HINT}"
            )
        if status.get("initialized") is False:
            return None, "unknown", f"codegraph index not initialized; {_SETUP_GRAPH_HINT}"
        if not _is_fresh(status):
            return None, "stale", f"codegraph index stale or worktree-mismatched; {_SETUP_GRAPH_HINT}"
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return None, "unknown", f"codegraph DB not found; {_SETUP_GRAPH_HINT}"
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

    def top_symbols_in_file(self, repo_root: str, file_path: str, *, limit: int = 10) -> dict[str, Any]:
        """Top structural symbols in one file by inbound fan-in. Path matching is normalized so a
        Windows-shaped ``src\\x.py`` request matches codegraph's repo-relative POSIX path."""
        con, freshness, reason = self._open(repo_root)
        normalized = str(file_path or "").replace("\\", "/")
        if con is None:
            return {
                "available": False, "graph_freshness": freshness, "fallback_reason": reason,
                "file_path": normalized, "symbols": [], "truncated": False, "total_symbol_count": 0,
            }
        try:
            node_ph = ",".join("?" * len(_STRUCTURAL_NODE_KINDS))
            edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
            rows, total = self._top_symbols_in_file_rows(
                con, node_ph, edge_ph, normalized, max(1, limit)
            )
            return {
                "available": True,
                "graph_freshness": "fresh",
                "fallback_reason": None,
                "file_path": normalized,
                "symbols": [self._symbol_node(r) for r in rows],
                "truncated": total > len(rows),
                "total_symbol_count": total,
            }
        except (sqlite3.Error, OSError) as exc:
            return {
                "available": False, "graph_freshness": "unknown",
                "fallback_reason": f"codegraph DB query failed: {exc}",
                "file_path": normalized, "symbols": [], "truncated": False, "total_symbol_count": 0,
            }
        finally:
            con.close()

    def god_node_map(
        self,
        repo_root: str,
        *,
        max_files: int = _DEFAULT_GODMAP_MAX_FILES,
        max_symbols_per_file: int = _DEFAULT_GODMAP_SYMBOLS_PER_FILE,
        max_nodes: int = _DEFAULT_GODMAP_MAX_NODES,
        max_edges: int = _DEFAULT_GODMAP_MAX_EDGES,
    ) -> dict[str, Any]:
        """Hub-and-spoke whole-repo map: hot files as rectangle hubs, top symbols as circles.

        This is the readable default for large repos. It is bounded by construction and uses only real
        CodeGraph structure: synthetic dashed file→symbol spokes plus real displayed symbol→symbol
        impact edges.
        """
        con, freshness, reason = self._open(repo_root)
        if con is None:
            return _empty(
                False, freshness, reason, mode="godmap", collapsed=False,
                total_file_count=0, total_symbol_count=0, total_edge_count=0,
            )
        try:
            max_files = max(1, max_files)
            max_symbols_per_file = max(1, max_symbols_per_file)
            max_nodes = max(1, max_nodes)
            max_edges = max(1, max_edges)
            node_ph = ",".join("?" * len(_STRUCTURAL_NODE_KINDS))
            fanin_edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
            structural_edge_ph = ",".join("?" * len(_STRUCTURAL_EDGE_KINDS))
            total_symbol_count = int(
                con.execute(
                    f"SELECT COUNT(*) AS c FROM nodes WHERE kind IN ({node_ph})",
                    _STRUCTURAL_NODE_KINDS,
                ).fetchone()["c"]
            )
            file_rows = self._hot_file_rows(con)
            total_file_count = len(file_rows)
            total_node_count = total_file_count + sum(int(r["symbol_count"]) for r in file_rows)
            total_edge_count = (
                sum(int(r["symbol_count"]) for r in file_rows)
                + self._godmap_total_cross_edges(con, node_ph, structural_edge_ph, file_rows)
            )
            file_rows = file_rows[:max_files]

            nodes: list[dict[str, Any]] = []
            symbol_ids: list[str] = []
            truncated = total_file_count > len(file_rows)
            for rank, file_row in enumerate(file_rows):
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                file_path_norm = (file_row["f"] or "").replace("\\", "/")
                hub_id = f"file:{file_path_norm}"
                nodes.append({
                    "id": hub_id,
                    "kind": "file_hub",
                    "graph_role": "hub",
                    "shape": "rectangle",
                    "qualified_name": None,
                    "file_path": file_path_norm,
                    "label": file_path_norm.rsplit("/", 1)[-1],
                    "symbol_count": int(file_row["symbol_count"]),
                    "inbound_count": int(file_row["callers"]),
                    "hub_rank": rank,
                })
                remaining = max_nodes - len(nodes)
                if remaining <= 0:
                    truncated = True
                    break
                limit = min(max_symbols_per_file, remaining)
                symbol_rows, total_in_file = self._top_symbols_in_file_rows(
                    con, node_ph, fanin_edge_ph, file_path_norm, limit
                )
                if total_in_file > len(symbol_rows):
                    truncated = True
                for r in symbol_rows:
                    symbol = self._symbol_node(r)
                    symbol["graph_role"] = "symbol"
                    symbol["shape"] = "ellipse"
                    symbol["hub_id"] = hub_id
                    symbol["hub_rank"] = rank
                    nodes.append(symbol)
                    symbol_ids.append(symbol["id"])

            edges: list[dict[str, Any]] = []
            symbol_nodes = [n for n in nodes if n.get("graph_role") == "symbol"]
            for n in symbol_nodes:
                if len(edges) >= max_edges:
                    truncated = True
                    break
                edges.append({
                    "source": n["hub_id"],
                    "target": n["id"],
                    "kind": "contains",
                    "edge_type": "spoke",
                    "line_style": "dashed",
                    "weight": 1,
                    "hub_rank": n["hub_rank"],
                })
            cross_limit = max_edges - len(edges)
            if symbol_ids and cross_limit > 0:
                edge_rows = self._displayed_symbol_edge_rows(
                    con, structural_edge_ph, symbol_ids, cross_limit
                )
                for r in edge_rows:
                    edges.append({
                        "source": r["source"],
                        "target": r["target"],
                        "kind": r["kind"],
                        "edge_type": "cross_symbol",
                        "line_style": "solid",
                        "weight": 1,
                    })
                if len(edge_rows) >= cross_limit:
                    # Conservative: the query was capped; there may be more displayed-symbol edges.
                    truncated = True

            return {
                "available": True,
                "graph_freshness": "fresh",
                "fallback_reason": None,
                "mode": "godmap",
                "collapsed": False,
                "nodes": nodes,
                "edges": edges,
                "truncated": truncated,
                "total_file_count": total_file_count,
                "total_symbol_count": total_symbol_count,
                "rendered_node_count": len(nodes),
                "rendered_edge_count": len(edges),
                "total_node_count": total_node_count,
                "total_edge_count": total_edge_count,
            }
        except (sqlite3.Error, OSError) as exc:
            return _empty(
                False, "unknown", f"codegraph DB query failed: {exc}",
                mode="godmap", collapsed=False, total_file_count=0,
                total_symbol_count=0, total_edge_count=0,
            )
        finally:
            con.close()

    @staticmethod
    def _hot_file_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
        call_ph = ",".join("?" * len(_CALLABLE_KINDS))
        node_ph = ",".join("?" * len(_STRUCTURAL_NODE_KINDS))
        edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
        return con.execute(
            f"SELECT h.f AS f, h.callers AS callers, COUNT(DISTINCT n.id) AS symbol_count "
            f"FROM ("
            f"  SELECT tgt.file_path AS f, COUNT(DISTINCT e.source) AS callers "
            f"  FROM nodes tgt JOIN edges e ON e.target = tgt.id "
            f"  WHERE tgt.kind IN ({call_ph}) AND e.kind IN ({edge_ph}) AND tgt.file_path IS NOT NULL "
            f"  GROUP BY tgt.file_path HAVING callers > 0"
            f") h "
            f"JOIN nodes n ON n.file_path = h.f AND n.kind IN ({node_ph}) "
            f"GROUP BY h.f, h.callers "
            f"ORDER BY h.callers DESC, replace(h.f, '\\', '/') ASC",
            (*_CALLABLE_KINDS, *_FANIN_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS),
        ).fetchall()

    @staticmethod
    def _godmap_total_cross_edges(
        con: sqlite3.Connection, node_ph: str, edge_ph: str, file_rows: list[sqlite3.Row]
    ) -> int:
        files = [(r["f"] or "").replace("\\", "/") for r in file_rows]
        if not files:
            return 0
        file_ph = ",".join("?" * len(files))
        return int(
            con.execute(
                f"SELECT COUNT(*) AS c FROM edges e "
                f"JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
                f"WHERE e.kind IN ({edge_ph}) AND s.kind IN ({node_ph}) AND t.kind IN ({node_ph}) "
                f"AND replace(s.file_path, '\\', '/') IN ({file_ph}) "
                f"AND replace(t.file_path, '\\', '/') IN ({file_ph})",
                (*_STRUCTURAL_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS, *_STRUCTURAL_NODE_KINDS, *files, *files),
            ).fetchone()["c"]
        )

    @staticmethod
    def _top_symbols_in_file_rows(
        con: sqlite3.Connection, node_ph: str, edge_ph: str, file_path: str, limit: int
    ) -> tuple[list[sqlite3.Row], int]:
        total = int(
            con.execute(
                f"SELECT COUNT(*) AS c FROM nodes WHERE kind IN ({node_ph}) "
                f"AND replace(file_path, '\\', '/') = ?",
                (*_STRUCTURAL_NODE_KINDS, file_path),
            ).fetchone()["c"]
        )
        rows = con.execute(
            f"SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, "
            f"COUNT(DISTINCT si.id) AS inbound_count, COUNT(DISTINCT ot.id) AS outbound_count "
            f"FROM nodes n "
            f"LEFT JOIN edges ei ON ei.target = n.id AND ei.kind IN ({edge_ph}) "
            f"LEFT JOIN nodes si ON si.id = ei.source AND si.kind IN ({node_ph}) "
            f"LEFT JOIN edges eo ON eo.source = n.id AND eo.kind IN ({edge_ph}) "
            f"LEFT JOIN nodes ot ON ot.id = eo.target AND ot.kind IN ({node_ph}) "
            f"WHERE n.kind IN ({node_ph}) AND replace(n.file_path, '\\', '/') = ? "
            f"GROUP BY n.id "
            f"ORDER BY inbound_count DESC, outbound_count DESC, n.qualified_name ASC, n.id ASC LIMIT ?",
            (
                *_FANIN_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS,
                *_FANIN_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS,
                *_STRUCTURAL_NODE_KINDS, file_path, limit,
            ),
        ).fetchall()
        return rows, total

    @staticmethod
    def _displayed_symbol_edge_rows(
        con: sqlite3.Connection, edge_ph: str, symbol_ids: list[str], limit: int
    ) -> list[sqlite3.Row]:
        id_ph = ",".join("?" * len(symbol_ids))
        return con.execute(
            f"SELECT source, target, kind FROM edges "
            f"WHERE source IN ({id_ph}) AND target IN ({id_ph}) AND kind IN ({edge_ph}) "
            f"ORDER BY source, target, kind LIMIT ?",
            (*symbol_ids, *symbol_ids, *_STRUCTURAL_EDGE_KINDS, limit),
        ).fetchall()

    @staticmethod
    def _symbol_node(row: sqlite3.Row) -> dict[str, Any]:
        inbound = int(row["inbound_count"])
        outbound = int(row["outbound_count"])
        return {
            "id": row["id"],
            "kind": row["kind"],
            "qualified_name": row["qualified_name"],
            "file_path": (row["file_path"] or "").replace("\\", "/") or None,
            "label": _short_label(row["qualified_name"], row["name"]),
            "inbound_count": inbound,
            "outbound_count": outbound,
            "degree": inbound + outbound,
        }

    def full_graph(
        self,
        repo_root: str,
        *,
        max_nodes: int = _DEFAULT_FULL_GRAPH_MAX_NODES,
        max_edges: int = 40000,
        collapse_after: int = _DEFAULT_FULL_GRAPH_COLLAPSE_AFTER,
    ) -> dict[str, Any]:
        """Whole-repo structural graph, bounded and fail-soft.

        Two deterministic modes, same envelope shape:
          * ``"symbol"`` — one node per structural symbol, when the true owner-node count is
            ``<= collapse_after``.
          * ``"file"`` — one node per file with symbol edges aggregated to weighted file-to-file
            edges, when the count exceeds ``collapse_after``.

        ``total_node_count`` / ``total_edge_count`` are the true whole-repo counts (pre-cap), so a
        capped or collapsed render can honestly say "showing X of Y". Never raises.
        """
        con, freshness, reason = self._open(repo_root)
        if con is None:
            return _empty(
                False, freshness, reason, mode="symbol", collapsed=False, total_edge_count=0
            )
        try:
            max_nodes = max(1, max_nodes)
            max_edges = max(1, max_edges)
            collapse_after = max(1, collapse_after)
            node_ph = ",".join("?" * len(_STRUCTURAL_NODE_KINDS))
            edge_ph = ",".join("?" * len(_STRUCTURAL_EDGE_KINDS))
            total_node_count = int(
                con.execute(
                    f"SELECT COUNT(*) AS c FROM nodes WHERE kind IN ({node_ph})",
                    _STRUCTURAL_NODE_KINDS,
                ).fetchone()["c"]
            )
            total_edge_count = int(
                con.execute(
                    f"SELECT COUNT(*) AS c FROM edges e "
                    f"JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
                    f"WHERE e.kind IN ({edge_ph}) AND s.kind IN ({node_ph}) AND t.kind IN ({node_ph})",
                    (*_STRUCTURAL_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS, *_STRUCTURAL_NODE_KINDS),
                ).fetchone()["c"]
            )
            if total_node_count > collapse_after:
                return self._file_mode(
                    con, node_ph, edge_ph, max_nodes, max_edges, total_node_count, total_edge_count
                )
            return self._symbol_mode(
                con, node_ph, edge_ph, max_nodes, max_edges, total_node_count, total_edge_count
            )
        except (sqlite3.Error, OSError) as exc:
            return _empty(
                False, "unknown", f"codegraph DB query failed: {exc}",
                mode="symbol", collapsed=False, total_edge_count=0,
            )
        finally:
            con.close()

    @staticmethod
    def _symbol_mode(
        con: sqlite3.Connection, node_ph: str, edge_ph: str,
        max_nodes: int, max_edges: int, total_node_count: int, total_edge_count: int,
    ) -> dict[str, Any]:
        node_rows = con.execute(
            f"SELECT id, kind, name, qualified_name, file_path FROM nodes "
            f"WHERE kind IN ({node_ph}) ORDER BY id LIMIT ?",
            (*_STRUCTURAL_NODE_KINDS, max_nodes + 1),
        ).fetchall()
        truncated = len(node_rows) > max_nodes
        node_rows = node_rows[:max_nodes]
        ids = [r["id"] for r in node_rows]
        edges: list[dict[str, Any]] = []
        inbound: dict[str, int] = {}
        outbound: dict[str, int] = {}
        if ids:
            id_ph = ",".join("?" * len(ids))
            edge_rows = con.execute(
                f"SELECT source, target, kind FROM edges "
                f"WHERE source IN ({id_ph}) AND target IN ({id_ph}) AND kind IN ({edge_ph}) "
                f"ORDER BY source, target, kind LIMIT ?",
                (*ids, *ids, *_STRUCTURAL_EDGE_KINDS, max_edges + 1),
            ).fetchall()
            if len(edge_rows) > max_edges:
                truncated = True
                edge_rows = edge_rows[:max_edges]
            for r in edge_rows:
                edges.append({"source": r["source"], "target": r["target"], "kind": r["kind"]})
                outbound[r["source"]] = outbound.get(r["source"], 0) + 1
                inbound[r["target"]] = inbound.get(r["target"], 0) + 1
        nodes = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "qualified_name": r["qualified_name"],
                "file_path": (r["file_path"] or "").replace("\\", "/") or None,
                "label": _short_label(r["qualified_name"], r["name"]),
                "inbound_count": inbound.get(r["id"], 0),
                "outbound_count": outbound.get(r["id"], 0),
                "degree": inbound.get(r["id"], 0) + outbound.get(r["id"], 0),
            }
            for r in node_rows
        ]
        return {
            "available": True,
            "graph_freshness": "fresh",
            "fallback_reason": None,
            "mode": "symbol",
            "collapsed": False,
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
            "total_node_count": total_node_count,
            "total_edge_count": total_edge_count,
        }

    @staticmethod
    def _file_mode(
        con: sqlite3.Connection, node_ph: str, edge_ph: str,
        max_nodes: int, max_edges: int, total_node_count: int, total_edge_count: int,
    ) -> dict[str, Any]:
        total_file_count = int(
            con.execute(
                f"SELECT COUNT(DISTINCT file_path) AS c FROM nodes "
                f"WHERE kind IN ({node_ph}) AND file_path IS NOT NULL",
                _STRUCTURAL_NODE_KINDS,
            ).fetchone()["c"]
        )
        file_rows = con.execute(
            f"SELECT n.file_path AS f, COUNT(DISTINCT n.id) AS symbol_count, "
            f"COUNT(DISTINCT s.id) AS inbound_count FROM nodes n "
            f"LEFT JOIN edges e ON e.target = n.id AND e.kind IN ({edge_ph}) "
            f"LEFT JOIN nodes s ON s.id = e.source AND s.kind IN ({node_ph}) "
            f"WHERE n.kind IN ({node_ph}) AND n.file_path IS NOT NULL "
            f"GROUP BY n.file_path "
            f"ORDER BY inbound_count DESC, symbol_count DESC, replace(n.file_path, '\\', '/') ASC "
            f"LIMIT ?",
            (*_STRUCTURAL_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS, *_STRUCTURAL_NODE_KINDS, max_nodes + 1),
        ).fetchall()
        truncated = len(file_rows) > max_nodes
        file_rows = file_rows[:max_nodes]
        nodes = [
            {
                "id": (r["f"] or "").replace("\\", "/"),
                "kind": "file",
                "qualified_name": None,
                "file_path": (r["f"] or "").replace("\\", "/"),
                "label": (r["f"] or "").replace("\\", "/").rsplit("/", 1)[-1],
                "symbol_count": int(r["symbol_count"]),
                "inbound_count": int(r["inbound_count"]),
            }
            for r in file_rows
        ]
        kept = {n["id"] for n in nodes}
        # Restrict the aggregation to the SAME capped ("kept") file set as the nodes BEFORE ORDER BY /
        # LIMIT — via a CTE that reproduces the file-node cap and joins both edge endpoints against it.
        # This mirrors symbol mode: the LIMIT budget is only ever spent on genuinely renderable
        # kept->kept pairs, so a dropped over-cap file can neither starve a real edge nor make
        # ``truncated`` dishonest, and no edge can dangle onto a file node that was capped out.
        edge_rows = con.execute(
            f"WITH kept AS ("
            f"  SELECT n.file_path AS raw, replace(n.file_path, '\\', '/') AS norm, "
            f"  COUNT(DISTINCT n.id) AS symbol_count, COUNT(DISTINCT s0.id) AS inbound_count FROM nodes n "
            f"  LEFT JOIN edges e0 ON e0.target = n.id AND e0.kind IN ({edge_ph}) "
            f"  LEFT JOIN nodes s0 ON s0.id = e0.source AND s0.kind IN ({node_ph}) "
            f"  WHERE n.kind IN ({node_ph}) AND n.file_path IS NOT NULL "
            f"  GROUP BY n.file_path "
            f"  ORDER BY inbound_count DESC, symbol_count DESC, replace(n.file_path, '\\', '/') ASC "
            f"  LIMIT ?"
            f") "
            f"SELECT ks.norm AS src, kt.norm AS dst, COUNT(*) AS weight FROM edges e "
            f"JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
            f"JOIN kept ks ON ks.raw = s.file_path JOIN kept kt ON kt.raw = t.file_path "
            f"WHERE e.kind IN ({edge_ph}) AND ks.norm != kt.norm "
            f"GROUP BY src, dst ORDER BY src, dst LIMIT ?",
            (
                *_STRUCTURAL_EDGE_KINDS, *_STRUCTURAL_NODE_KINDS, *_STRUCTURAL_NODE_KINDS, max_nodes,
                *_STRUCTURAL_EDGE_KINDS, max_edges + 1,
            ),
        ).fetchall()
        if len(edge_rows) > max_edges:
            truncated = True
            edge_rows = edge_rows[:max_edges]
        edges = [
            {"source": r["src"], "target": r["dst"], "kind": "file_aggregate", "weight": int(r["weight"])}
            for r in edge_rows
            if r["src"] in kept and r["dst"] in kept  # defensive: SQL already restricts to kept files
        ]
        return {
            "available": True,
            "graph_freshness": "fresh",
            "fallback_reason": None,
            "mode": "file",
            "collapsed": True,
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
            "total_node_count": total_node_count,
            "total_file_count": total_file_count,
            "total_edge_count": total_edge_count,
        }
