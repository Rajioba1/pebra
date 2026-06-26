"""import_graph_cache (Slice 3a) — the single cached import graph both walkers derive from.

Replaces the two separate full-repo scans (architecture_map + ast_import_graph) with one artifact at
``<repo>/.pebra/import_graph.json``, rebuilt **incrementally by per-file content hash** (technique
adapted from graphify's SHA256 cache, reimplemented in stdlib): only files whose bytes changed are
re-parsed; clean files' edges are carried forward. Freshness is git-agnostic — HEAD is never part of
the decision (it stays as provenance on ArchitectureEvidence, set by the caller).

  FRESH   — first build, or every file hash matches the cache
  REBUILT — some files changed/added/deleted and were re-parsed successfully (still trustworthy)
  STALE   — the (re)build itself failed (scan/parse raised) — evidence can't be vouched for
  UNKNOWN — no graph exists (empty or missing repo)

Adapter layer: stdlib only (hashlib/json/os/pathlib). Imports core constants + the shared resolver.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pebra.adapters._ast_utils import (
    EDGE_CONFIDENCE,
    _top_level_names,
    parse_facts,
    python_files,
)
from pebra.core.constants import (
    ANCHOR_FANIN_PERCENTILE,
    ANCHOR_MIN_IN_DEGREE,
    GraphFreshness,
)

# v5: parse_error_files (unparseable sources as graph uncertainty). v4: structural metrics (3f) —
# percentile+floor anchors, per-file god_node_scores, out_degree, and cycle_files (SCC). v3: per-file
# entrypoint flag (3e). v2: unresolved edges kept individually + external/internal split. Any older
# cache is rejected on load and rebuilt.
SCHEMA_VERSION = "5"
ANALYZER_VERSION = "1"
_CACHE_REL = Path(".pebra") / "import_graph.json"


def _domain(posix_path: str) -> str:
    return posix_path.split("/", 1)[0] if "/" in posix_path else "."


def _file_sha256(root: Path, rel: str) -> str:
    # content-only hash: path is the dict key (metadata), not part of content identity — same bytes
    # in two files are logically the same content.
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()


def _parse_file(
    root: Path, rel: str, fileset: set[str], top_level_names: set[str]
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Parse one file once -> (edges, is_entrypoint, parse_error). Resolved targets are deduped
    (highest-confidence kind per target); unresolved (tgt=None) edges are kept individually so their
    counts/names survive for 3c/3d — collapsing them by target would lose how many unresolved/dynamic/
    external imports."""
    source = (root / rel).read_text(encoding="utf-8", errors="replace")
    raw_edges, is_ep, parse_error = parse_facts(rel, source, fileset, top_level_names)
    best: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    for edge in raw_edges:
        if edge.target == rel:
            continue
        if edge.target is None:
            # keep the import name (3d) so model guidance can say WHAT failed to resolve.
            unresolved.append({"src": rel, "tgt": None, "kind": edge.kind, "name": edge.name})
            continue
        prev = best.get(edge.target)
        if prev is None or EDGE_CONFIDENCE.get(edge.kind, 0.10) > EDGE_CONFIDENCE.get(prev, 0.10):
            best[edge.target] = edge.kind
    edges = [{"src": rel, "tgt": tgt, "kind": kind} for tgt, kind in best.items()] + unresolved
    return edges, is_ep, parse_error


def _parse_many(
    root: Path, files: list[str], fileset: set[str], top_level_names: set[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Parse several files -> (all edges, entrypoint files, parse-error files)."""
    edges: list[dict[str, Any]] = []
    entrypoints: list[str] = []
    parse_errors: list[str] = []
    for f in files:
        file_edges, is_ep, parse_error = _parse_file(root, f, fileset, top_level_names)
        edges.extend(file_edges)
        if is_ep:
            entrypoints.append(f)
        if parse_error:
            parse_errors.append(f)
    return edges, entrypoints, parse_errors


def _fanin_percentiles(
    in_degree: dict[str, int], total_files: int
) -> dict[str, float]:
    """Repo-relative fan-in percentile per file with in-degree >= floor (the god-node candidates).
    Percentile rank = fraction of files (incl. zero-in-degree leaves) whose in-degree is <= this
    file's. Robust to a single outlier, unlike in_degree/max. Files below the floor are omitted (0)."""
    if total_files <= 0:
        return {}
    zeros = total_files - len(in_degree)
    distribution = sorted([0] * zeros + list(in_degree.values()))
    return {
        f: bisect.bisect_right(distribution, d) / total_files
        for f, d in in_degree.items()
        if d >= ANCHOR_MIN_IN_DEGREE
    }


def _cycle_files(edges: list[dict[str, Any]]) -> list[str]:
    """Files participating in an import cycle (Tarjan SCC of size > 1), over the forward graph.
    Iterative to stay safe on deep graphs. Self-edges are already excluded upstream."""
    adj: dict[str, list[str]] = {}
    nodes: set[str] = set()
    for e in edges:
        if e["tgt"] is not None:
            adj.setdefault(e["src"], []).append(e["tgt"])
            nodes.add(e["src"])
            nodes.add(e["tgt"])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    cyclic: set[str] = set()
    counter = 0
    for start in sorted(nodes):
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            neighbors = adj.get(v, ())
            recursed = False
            i = pi
            while i < len(neighbors):
                w = neighbors[i]
                if w not in index:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recursed = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
                i += 1
            if recursed:
                continue
            if low[v] == index[v]:  # SCC root
                component: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                if len(component) > 1:
                    cyclic.update(component)
            work.pop()
            if work:  # propagate low-link to parent
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return sorted(cyclic)


def _assemble(
    file_hashes: dict[str, str],
    edges: list[dict[str, Any]],
    entrypoints: list[str] | tuple[str, ...] = (),
    parse_error_files: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    in_degree: dict[str, int] = {}
    out_degree: dict[str, int] = {}
    total_edges = 0
    cross_domain_edges = 0
    for e in edges:
        tgt = e["tgt"]
        if tgt is None:
            continue
        in_degree[tgt] = in_degree.get(tgt, 0) + 1
        out_degree[e["src"]] = out_degree.get(e["src"], 0) + 1
        total_edges += 1
        if _domain(e["src"]) != _domain(tgt):
            cross_domain_edges += 1
    # 3f: anchors require both the in-degree floor AND a top-percentile fan-in (repo-relative).
    god_node_scores = _fanin_percentiles(in_degree, len(file_hashes))
    anchors = sorted(f for f, s in god_node_scores.items() if s >= ANCHOR_FANIN_PERCENTILE)
    return {
        "schema_version": SCHEMA_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "file_hashes": file_hashes,
        "edges": edges,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "god_node_scores": god_node_scores,
        "anchors": anchors,
        "cycle_files": _cycle_files(edges),
        "total_edges": total_edges,
        "cross_domain_edges": cross_domain_edges,
        "entrypoints": sorted(set(entrypoints)),
        "parse_error_files": sorted(set(parse_error_files)),
    }


def build_import_graph(
    root: Path, *, prev_cache: dict[str, Any] | None = None
) -> tuple[dict[str, Any], GraphFreshness]:
    """Incrementally (re)build the import graph. Pure of cache-file I/O — caller loads/saves."""
    try:
        files = python_files(root)
        if not files:
            return _assemble({}, []), GraphFreshness.UNKNOWN
        fileset = set(files)
        top_level = _top_level_names(fileset)
        current_hashes = {f: _file_sha256(root, f) for f in files}

        if prev_cache is None:
            edges, entrypoints, parse_errors = _parse_many(root, files, fileset, top_level)
            return _assemble(current_hashes, edges, entrypoints, parse_errors), GraphFreshness.FRESH

        prev_hashes: dict[str, str] = prev_cache.get("file_hashes", {})
        prev_edges: list[dict[str, Any]] = prev_cache.get("edges", [])
        prev_entrypoints: list[str] = prev_cache.get("entrypoints", [])
        prev_parse_errors: list[str] = prev_cache.get("parse_error_files", [])
        fileset_changed = set(prev_hashes) != fileset
        modified = {f for f in files if prev_hashes.get(f) != current_hashes[f]}

        if not fileset_changed and not modified:
            return (
                _assemble(current_hashes, prev_edges, prev_entrypoints, prev_parse_errors),
                GraphFreshness.FRESH,
            )

        if fileset_changed:
            # Files were added/removed, which can change import RESOLUTION for files we did NOT touch
            # (a new target an existing importer now resolves to; a deleted target that would leave a
            # phantom edge). Carrying clean edges by source would be wrong, so do a full rebuild.
            edges, entrypoints, parse_errors = _parse_many(root, files, fileset, top_level)
            return _assemble(current_hashes, edges, entrypoints, parse_errors), GraphFreshness.REBUILT

        # content-only edits within a stable fileset: resolution is unchanged for clean files, so
        # carry their edges/entrypoint flags and re-parse only the modified ones.
        carried = [e for e in prev_edges if e["src"] not in modified]
        reparsed, reparsed_eps, reparsed_parse_errors = _parse_many(
            root, sorted(modified), fileset, top_level
        )
        entrypoints = [f for f in prev_entrypoints if f not in modified] + reparsed_eps
        parse_errors = [f for f in prev_parse_errors if f not in modified] + reparsed_parse_errors
        return _assemble(current_hashes, carried + reparsed, entrypoints, parse_errors), GraphFreshness.REBUILT
    except (OSError, RecursionError, MemoryError):
        # the (re)build failed — we cannot vouch for the graph
        return (prev_cache if prev_cache is not None else _assemble({}, [])), GraphFreshness.STALE


def load_import_graph(root: Path) -> dict[str, Any] | None:
    cache_path = root / _CACHE_REL
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    if not isinstance(data.get("file_hashes"), dict):
        return None
    if not isinstance(data.get("parse_error_files", []), list):
        return None
    edges = data.get("edges")
    if not isinstance(edges, list):
        return None
    # Every edge must be a well-formed dict — a list of junk (e.g. [1, 2]) or an edge missing a key
    # would otherwise reach _assemble/derive_reverse and crash on e["tgt"]. Reject -> rebuild.
    if not all(isinstance(e, dict) and {"src", "tgt", "kind"} <= e.keys() for e in edges):
        return None
    return data


def save_import_graph(root: Path, payload: dict[str, Any]) -> None:
    cache_path = root / _CACHE_REL
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, cache_path)  # atomic on the same volume
    except OSError:
        pass  # the in-memory graph is still valid for this assessment


def get_import_graph(root: Path) -> tuple[dict[str, Any], GraphFreshness]:
    """High-level entry: load cache, incrementally rebuild, persist (when rebuilt), return graph."""
    prev = load_import_graph(root)
    payload, freshness = build_import_graph(root, prev_cache=prev)
    if freshness in (GraphFreshness.FRESH, GraphFreshness.REBUILT) and payload.get("file_hashes"):
        if prev is None or freshness is GraphFreshness.REBUILT:
            save_import_graph(root, payload)
    return payload, freshness


def derive_reverse(edges: list[dict[str, Any]]) -> dict[str, list[tuple[str, float]]]:
    """Reverse dependency map (target -> [(importer, edge_confidence)]) for the blast walk."""
    reverse: dict[str, list[tuple[str, float]]] = {}
    for e in edges:
        if e["tgt"] is not None:
            reverse.setdefault(e["tgt"], []).append((e["src"], EDGE_CONFIDENCE.get(e["kind"], 0.10)))
    return reverse
