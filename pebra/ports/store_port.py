"""StorePort (Architecture §3, §10). Protocol contract only.

Persists assessments + guidance packets in a hash-chained, append-only store and verifies the chain.
"""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import AssessmentResult


class StorePort(Protocol):
    def persist_assessment(
        self, result: AssessmentResult, request_payload: dict[str, Any]
    ) -> str:
        """Append an assessment (and its guidance packet) to the hash chain; return its id."""
        ...

    def validate_chain(self) -> bool:
        """Return True iff the stored hash chain is intact (tamper-evident)."""
        ...

    def load_assessment(self, assessment_id: str) -> dict[str, Any]:
        """Return the stored assessment content (decision, scores, guidance packet, assessed_commit)."""
        ...

    def persist_guardrails(self, assessment_id: str, guardrails: dict[str, Any]) -> str:
        """Append a post_assessment_guardrails row and return its id."""
        ...

    def active_sanction_for_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        """Return the active sanction bound to an assessment, or None (AD-26 verify-side lookup)."""
        ...

    def invalidate_sanctions_for_assessment(self, assessment_id: str, reason: str) -> list[str]:
        """Invalidate active sanctions bound to an assessment on drift (AD-26); return their ids."""
        ...
