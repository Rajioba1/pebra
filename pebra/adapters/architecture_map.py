"""architecture_map (AD-22) — PEBRA's self-built repo structure map (ArchitectureKnowledgeProvider).

Derives structural signals (god-node/anchor, domains, bridge centrality) + a freshness verdict from
the shared, content-hash-cached import graph (``import_graph_cache``). Freshness is git-agnostic:
FRESH/REBUILT when the content-hashed graph is up to date, STALE when its (re)build failed, UNKNOWN
when there is no graph to build (empty/missing repo). current_head is kept only as provenance.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from pebra.adapters.import_graph_cache import get_import_graph
from pebra.core.constants import STAGE_MAP, GraphFreshness
from pebra.core.models import ArchitectureEvidence
from pebra.ports.config_port import CriticalityGlob


def _domain(posix_path: str) -> str:
    return posix_path.split("/", 1)[0] if "/" in posix_path else "."


class ArchitectureMapAdapter:
    def __init__(self, criticality_globs: list[CriticalityGlob] | None = None) -> None:
        self._globs = list(criticality_globs or [])

    # --- public port method ---

    def gather_architecture(
        self, repo_root: str, affected_files: list[str], current_head: str | None
    ) -> ArchitectureEvidence:
        root = Path(repo_root)
        if not root.is_dir():
            return ArchitectureEvidence(domain_criticality_hint=self._hint(affected_files))
        # One shared, content-hash-cached import graph (no separate scan). current_head is provenance
        # only — freshness is decided by per-file content hashes, not HEAD.
        payload, freshness = get_import_graph(root)
        if freshness is GraphFreshness.UNKNOWN:  # empty repo / nothing to map
            return ArchitectureEvidence(domain_criticality_hint=self._hint(affected_files))
        return self._evidence(payload, affected_files, freshness, current_head)

    # --- evidence assembly ---

    def _evidence(
        self,
        m: dict[str, Any],
        affected_files: list[str],
        freshness: GraphFreshness,
        graph_commit: str | None,
    ) -> ArchitectureEvidence:
        anchors = set(m.get("anchors", []))
        god_node_scores: dict[str, float] = m.get("god_node_scores", {})
        out_degree: dict[str, int] = m.get("out_degree", {})
        cycle_files = set(m.get("cycle_files", []))
        entrypoints = set(m.get("entrypoints", []))  # decorator/filename entrypoints (3e)
        matched_anchors = [f for f in affected_files if f in anchors]
        # 3f: structural signals are repo-relative percentiles + an in-degree floor (computed in the
        # cache). god_node_score = max fan-in percentile of the edited files; fan_out = their coupling.
        god = max((god_node_scores.get(f, 0.0) for f in affected_files), default=0.0)
        fan_out = max((out_degree.get(f, 0) for f in affected_files), default=0)
        total_edges = m.get("total_edges", 0)
        bridge = (m.get("cross_domain_edges", 0) / total_edges) if total_edges else 0.0
        return ArchitectureEvidence(
            graph_commit=graph_commit,
            graph_freshness=freshness,
            matched_anchors=matched_anchors,
            matched_domains=sorted({_domain(f) for f in affected_files}),
            architecture_anchor_score=(len(matched_anchors) / len(anchors)) if anchors else 0.0,
            god_node_score=god,
            bridge_centrality=bridge,
            domain_entrypoint=any(f in entrypoints for f in affected_files),
            fan_out=fan_out,
            cycle_participation=any(f in cycle_files for f in affected_files),
            domain_criticality_hint=self._hint(affected_files),
            source_files=list(affected_files),
        )

    def _hint(self, affected_files: list[str]) -> str | None:
        best: str | None = None
        for g in self._globs:
            if any(fnmatch(f, g.pattern) for f in affected_files):
                if best is None or STAGE_MAP.get(g.stage, 0.0) > STAGE_MAP.get(best, 0.0):
                    best = g.stage
        return best
