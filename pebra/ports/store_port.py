"""StorePort (Architecture §3, §10). Protocol contract only.

Persists assessments + guidance packets in a hash-chained, append-only store and verifies the chain.
"""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import AssessmentResult


class StorePort(Protocol):
    def persist_assessment(
        self,
        result: AssessmentResult,
        request_payload: dict[str, Any],
        predictions: list[dict[str, Any]] | None = None,
    ) -> str:
        """Append an assessment (and its guidance packet) to the hash chain; return its id. The
        optional ``predictions`` manifest (Milestone 4a) is written atomically with the assessment."""
        ...

    def validate_chain(self) -> bool:
        """Return True iff the stored hash chain is intact (tamper-evident)."""
        ...

    def load_assessment(self, assessment_id: str) -> dict[str, Any]:
        """Return the stored assessment content (decision, scores, guidance packet, assessed_commit)."""
        ...

    def revise_safer_attempt_count(
        self, repo_id: str, assessed_commit: str | None, target_files: list[str]
    ) -> int:
        """Count prior revise_safer assessments at the same repo HEAD covering all target files."""
        ...

    def load_predictions(self, assessment_id: str) -> list[dict[str, Any]]:
        """Return the captured prediction manifest for an assessment (Milestone 4a)."""
        ...

    def load_outcomes(self, assessment_id: str) -> list[dict[str, Any]]:
        """Return the terminal outcomes recorded for an assessment (oldest first)."""
        ...

    def assessment_detail(self, assessment_id: str) -> dict[str, Any]:
        """Return full detail (content, guidance packet, guardrails, outcomes) for an assessment."""
        ...

    def prediction_errors_exist(self, assessment_id: str) -> bool:
        """True iff shadow prediction-error rows have already been computed for this assessment
        (Milestone 4d idempotency guard)."""
        ...

    def persist_guardrails(self, assessment_id: str, guardrails: dict[str, Any]) -> str:
        """Append a post_assessment_guardrails row and return its id."""
        ...

    def active_sanction_for_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        """Return the active sanction bound to an assessment, or None (AD-26 verify-side lookup)."""
        ...

    def active_sanction_for_action(self, repo_id: str, action_id: str) -> dict[str, Any] | None:
        """Return the active sanction explicitly bound to an action, or None."""
        ...

    def invalidate_sanctions_for_assessment(self, assessment_id: str, reason: str) -> list[str]:
        """Invalidate active sanctions bound to an assessment on drift (AD-26); return their ids."""
        ...

    def latest_guardrails(self, assessment_id: str) -> dict[str, Any] | None:
        """Return the newest persisted verify/guardrails row for an assessment, or None."""
        ...

    def load_production_calibration_rows(
        self, repo_id: str | None = None, target_type: str = "risk_binary"
    ) -> list[dict[str, Any]]:
        """M5d promotion input: production-only (live, observed, proceeded, unguided) calibration rows.
        Each row carries a parsed ``features`` dict (the structural feature payload, ``{}`` if absent)."""
        ...

    def read_active_snapshot_rows(self, repo_id: str) -> dict[str, Any] | None:
        """The newest ACTIVE snapshot + its active, ratified learned facts as raw rows, or None.
        Used by the promotion drift-freeze (snapshot_reconciler) to compare active facts to the ledger."""
        ...
