"""Ports for exact-candidate authorization and working-tree application."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol


class CandidateGateDecision(Protocol):
    permission: str
    tier: str
    matched_assessment_id: str | None
    reason: str | None


class CandidateGatePort(Protocol):
    def decide(
        self,
        event: dict[str, Any],
        *,
        db_path: str,
        consult_only: bool,
        require_exact_match: bool = False,
    ) -> CandidateGateDecision: ...


class CandidateApplicationPort(Protocol):
    def lock(self, repo_root: str | Path) -> AbstractContextManager[None]: ...

    def apply(
        self,
        repo_root: str | Path,
        patch: str,
        *,
        expected_files: tuple[str, ...],
        acquire_lock: bool = True,
    ) -> tuple[str, ...]: ...
