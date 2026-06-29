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

    def write_promotion(
        self,
        repo_id: str,
        snapshot_metrics: dict[str, Any],
        facts: list[dict[str, Any]],
        snapshot_status: str = "active",
    ) -> tuple[str, list[str]]:
        """M5d: atomically append one risk_snapshot (status=snapshot_status) plus one or more
        learned_risk_facts (hash-chained). Returns ``(snapshot_id, [fact_ids])``."""
        ...
