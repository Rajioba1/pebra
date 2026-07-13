"""Port for bounded materialized-candidate graph-risk evidence."""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import CandidateAction, CandidateGraphRiskEvidence, GraphRiskScope


class GraphRiskRefinementProvider(Protocol):
    def analyze(
        self,
        action: CandidateAction,
        repo_root: str,
        scope: GraphRiskScope,
    ) -> CandidateGraphRiskEvidence: ...
