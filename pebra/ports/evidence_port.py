"""EvidenceProvider port (Architecture §3). Protocol contract only."""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import AssessmentRequest, CandidateAction, EvidenceBundle


class EvidenceProvider(Protocol):
    def gather_evidence(
        self, request: AssessmentRequest, action: CandidateAction, repo_root: str
    ) -> EvidenceBundle: ...
