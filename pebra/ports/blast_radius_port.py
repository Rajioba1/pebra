"""BlastRadiusProvider port (Architecture §3). Protocol contract only."""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import BlastEvidence, CandidateAction


class BlastRadiusProvider(Protocol):
    def blast(self, action: CandidateAction, repo_root: str) -> BlastEvidence: ...
