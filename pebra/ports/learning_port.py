"""LearningPort (Architecture §3, Milestone 4d). Protocol contract only.

The write surface for shadow learning measurement: computed prediction-error rows and a shadow risk
snapshot per measurement run. There is NO read-back into the assessment path — reapplying learning to
decisions is Milestone 5.
"""

from __future__ import annotations

from typing import Any, Protocol


class LearningPort(Protocol):
    def write_measurement(
        self,
        assessment_id: str,
        rows: list[dict[str, Any]],
        repo_id: str,
        metrics: dict[str, Any],
        status: str = "shadow",
    ) -> tuple[list[str], str]:
        """Atomically append prediction-error rows plus the shadow snapshot."""
        ...

    def write_prediction_errors(
        self, assessment_id: str, rows: list[dict[str, Any]]
    ) -> list[str]:
        """Append computed prediction-error rows for an assessment; return their ids."""
        ...

    def write_risk_snapshot(
        self, repo_id: str, metrics: dict[str, Any], status: str = "shadow"
    ) -> str:
        """Append a shadow snapshot recording a measurement run's metrics; return its id."""
        ...
