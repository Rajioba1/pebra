"""ast_import_graph (BlastRadiusProvider, AD-12) — stdlib AST blast-radius walk.

Blast radius = which files DEPEND on the changed file (the reverse import graph), to depth 3, with
depth buckets and per-edge confidence (static/relative/wildcard/dynamic weights). Request-supplied
blast evidence (the Phase-0 fixture path) still takes precedence so the worked example is unchanged.
Uses the shared resolver in ``_ast_utils`` so the dependency graph matches the architecture map.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from pebra.adapters.import_graph_cache import derive_reverse, get_import_graph
from pebra.core.constants import GRAPH_UNCERTAINTY_CAP, GRAPH_UNCERTAINTY_WEIGHTS
from pebra.core.models import BlastEvidence, CandidateAction

_ALLOWED = set(BlastEvidence.__dataclass_fields__)
_MAX_DEPTH = 3
_LOW_CONFIDENCE = 0.5
_INTERNAL_UNRESOLVED_KINDS = ("static", "relative")
_PROVENANCE_LIST_CAP = 25  # bound each guidance list so the model packet stays small


class AstImportGraphAdapter:
    def __init__(self, blast_evidence: dict[str, Any] | None = None) -> None:
        self._evidence = blast_evidence

    def blast(self, action: CandidateAction, repo_root: str) -> BlastEvidence:
        if self._evidence:
            return BlastEvidence(**{k: v for k, v in self._evidence.items() if k in _ALLOWED})

        root = Path(repo_root)
        if not root.is_dir():
            return BlastEvidence()

        # Derive the reverse-dependency graph from the shared, content-hash-cached import graph
        # (built once per assess by the architecture map; warm by the time blast runs).
        payload, _ = get_import_graph(root)
        fileset = set(payload.get("file_hashes", {}))
        if not fileset:
            return BlastEvidence()
        reverse = derive_reverse(payload["edges"])
        forward: dict[str, set[str]] = {}
        for e in payload["edges"]:
            if e["tgt"] is not None:
                forward.setdefault(e["src"], set()).add(e["tgt"])

        # An expected_file outside the repo fileset contributes zero blast — but 3c records it as a
        # missing-file signal (below) so "low blast" is not silently mistaken for "confidently safe".
        changed = [f for f in action.expected_files if f in fileset]
        entrypoints = set(payload.get("entrypoints", []))  # decorator/filename entrypoints (3e)
        result = self._walk(changed, reverse, forward, entrypoints)
        uncertainty = _graph_uncertainty(
            payload["edges"],
            changed,
            fileset,
            action.expected_files,
            set(payload.get("parse_error_files", [])),
        )
        return replace(result, **uncertainty)

    @staticmethod
    def _walk(
        changed: list[str],
        reverse: dict[str, list[tuple[str, float]]],
        forward: dict[str, set[str]],
        entrypoints: set[str],
    ) -> BlastEvidence:
        visited: set[str] = set(changed)
        depth_buckets: dict[int, int] = {}
        confidences: list[float] = []
        dq: deque[tuple[str, int]] = deque((c, 0) for c in changed)
        while dq:
            node, depth = dq.popleft()
            if depth >= _MAX_DEPTH:
                continue
            for importer, conf in reverse.get(node, []):
                # count the edge only when it first discovers a dependent, so the confidence stats
                # line up with the dependents in the blast (one edge per counted dependent) — a node
                # reached by several paths is not counted multiple times.
                if importer not in visited:
                    visited.add(importer)
                    confidences.append(conf)
                    nd = depth + 1
                    depth_buckets[nd] = depth_buckets.get(nd, 0) + 1
                    dq.append((importer, nd))

        dependents = visited - set(changed)
        direct = depth_buckets.get(1, 0)
        transitive = sum(v for d, v in depth_buckets.items() if d >= 2)
        return BlastEvidence(
            direct_count=direct,
            transitive_count=transitive,
            depth_buckets=depth_buckets,
            edge_confidence_mean=(sum(confidences) / len(confidences)) if confidences else 0.0,
            edge_confidence_min=min(confidences) if confidences else 0.0,
            low_confidence_edge_count=sum(1 for c in confidences if c < _LOW_CONFIDENCE),
            entrypoint_signal=any(f in entrypoints for f in changed)
            or any(d in entrypoints for d in dependents),
            import_cycle_detected=_has_cycle_from(changed, forward),
        )


def _has_cycle_from(starts: list[str], forward: dict[str, set[str]]) -> bool:
    """Detect a cycle reachable (forward) from the changed files. Iterative DFS with a recursion
    stack; scoped to the changed scope so an unrelated cycle elsewhere in the repo doesn't over-signal."""
    on_stack: set[str] = set()
    done: set[str] = set()
    for start in starts:
        # (node, iterator-not-started flag) emulated via explicit stack of (node, neighbors_left)
        stack: list[tuple[str, list[str]]] = [(start, sorted(forward.get(start, ())))]
        on_stack.add(start)
        while stack:
            node, neighbors = stack[-1]
            if neighbors:
                nxt = neighbors.pop()
                if nxt in on_stack:
                    return True
                if nxt not in done:
                    on_stack.add(nxt)
                    stack.append((nxt, sorted(forward.get(nxt, ()))))
            else:
                on_stack.discard(node)
                done.add(node)
                stack.pop()
    return False


def _graph_uncertainty(
    edges: list[dict[str, Any]],
    changed: list[str],
    fileset: set[str],
    expected_files: list[str],
    parse_error_files: set[str],
) -> dict[str, Any]:
    """Quantify how incomplete the blast estimate is. Edit-local counts come from edges out of the
    changed files; the repo_* terms count dynamic/wildcard imports ELSEWHERE that could hide a
    reverse dependency on the changed file. The score is a bounded additive penalty (capped) — never
    enough to collapse confidence to zero. External/stdlib imports are tracked but never penalized."""
    changed_set = set(changed)
    local = [e for e in edges if e["src"] in changed_set]
    unresolved = sum(
        1 for e in local if e["tgt"] is None and e["kind"] in _INTERNAL_UNRESOLVED_KINDS
    )
    dynamic = sum(1 for e in local if e["kind"] == "dynamic")
    wildcard = sum(1 for e in local if e["kind"] == "wildcard")
    external = sum(1 for e in local if e["kind"] == "external")
    missing = sum(1 for f in expected_files if f not in fileset)
    parse_errors = sorted(set(expected_files) & parse_error_files)
    # Whole-graph hidden-dependent risk only applies when the changed file actually exists in the
    # repo (something could secretly import it). For a ghost edit (no expected file present),
    # missing_file_count already captures the incompleteness — don't also charge full-graph repo risk.
    if changed_set:
        repo_dynamic = sum(
            1 for e in edges if e["kind"] == "dynamic" and e["src"] not in changed_set
        )
        repo_wildcard = sum(
            1 for e in edges if e["kind"] == "wildcard" and e["src"] not in changed_set
        )
    else:
        repo_dynamic = repo_wildcard = 0
    w = GRAPH_UNCERTAINTY_WEIGHTS
    score = min(
        GRAPH_UNCERTAINTY_CAP,
        w["missing_file"] * missing
        + w["parse_error_file"] * len(parse_errors)
        + w["unresolved_import"] * unresolved
        + w["dynamic_import"] * dynamic
        + w["wildcard_import"] * wildcard
        + w["repo_dynamic_import"] * repo_dynamic
        + w["repo_wildcard_import"] * repo_wildcard,
    )
    return {
        "missing_file_count": missing,
        "parse_error_count": len(parse_errors),
        "unresolved_import_count": unresolved,
        "dynamic_import_count": dynamic,
        "wildcard_import_count": wildcard,
        "external_import_count": external,
        "graph_uncertainty_score": score,
        "graph_uncertainty_reason": _uncertainty_reason(
            missing, len(parse_errors), unresolved, dynamic, wildcard, repo_dynamic + repo_wildcard
        )
        if score > 0
        else "",
        "unresolved_imports": _provenance(
            local, lambda e: e["tgt"] is None and e["kind"] in _INTERNAL_UNRESOLVED_KINDS
        ),
        "dynamic_imports": _provenance(local, lambda e: e["kind"] == "dynamic", default="<dynamic>"),
        "wildcard_imports": _provenance(local, lambda e: e["kind"] == "wildcard", default="*"),
        "missing_files": tuple(sorted(f for f in expected_files if f not in fileset))[
            :_PROVENANCE_LIST_CAP
        ],
        "parse_error_files": tuple(parse_errors)[:_PROVENANCE_LIST_CAP],
    }


def _provenance(
    local: list[dict[str, Any]], pred, default: str = "<unknown>"
) -> tuple[str, ...]:
    """Bounded, sorted, de-duplicated 'file: name' lines for the edges matching ``pred`` (3d)."""
    items = {f"{e['src']}: {e.get('name') or default}" for e in local if pred(e)}
    return tuple(sorted(items))[:_PROVENANCE_LIST_CAP]


def _uncertainty_reason(
    missing: int, parse_errors: int, unresolved: int, dynamic: int, wildcard: int, repo_hidden: int
) -> str:
    parts: list[str] = []
    if missing:
        parts.append(f"{missing} missing expected file(s)")
    if parse_errors:
        parts.append(f"{parse_errors} unparseable expected file(s)")
    if unresolved:
        parts.append(f"{unresolved} unresolved internal import(s)")
    if dynamic:
        parts.append(f"{dynamic} dynamic import(s)")
    if wildcard:
        parts.append(f"{wildcard} wildcard import(s)")
    if repo_hidden:
        parts.append(f"{repo_hidden} dynamic/wildcard import(s) elsewhere that may hide dependents")
    return "Graph evidence incomplete: " + "; ".join(parts) + "." if parts else ""
