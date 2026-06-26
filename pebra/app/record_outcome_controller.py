"""record_outcome_controller (Phase 3a, AD-4) — closes the action_status lifecycle.

Terminal action_status (completed/skipped/rejected) is written ONLY through this path: it validates
the status is terminal and appends an outcome record via OutcomePort. The assessment row stays
immutable (append-only chain); the current status is derived as the recorded outcome's terminal
status, else pending.
"""

from __future__ import annotations

from pebra.core import outcome_labels
from pebra.core.constants import ActionStatus
from pebra.ports.outcome_port import OutcomePort

_TERMINAL_STATUSES = frozenset(
    {ActionStatus.COMPLETED.value, ActionStatus.SKIPPED.value, ActionStatus.REJECTED.value}
)


def record_outcome(
    assessment_id: str,
    status: str,
    *,
    outcome_port: OutcomePort,
    detail: dict | None = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"outcome status must be terminal {sorted(_TERMINAL_STATUSES)}, got {status!r}"
        )
    # Milestone 4b: the detail may carry explicit learning labels (actual_success, event_outcomes,
    # benefit_realized, actual_review_cost, actual_rework_cost). Reject malformed labels before the
    # immutable write so a bad label never enters the chain. Absent labels are fine (-> censored).
    outcome_labels.validate_labels(detail)
    if status == ActionStatus.COMPLETED.value:
        latest = outcome_port.latest_guardrails(assessment_id)
        if latest is None:
            raise ValueError(
                "completed outcome requires a latest passing pebra verify result"
            )
        if latest.get("pre_commit_decision") != "proceed":
            raise ValueError(
                "completed outcome requires latest pebra verify pre_commit_decision='proceed'"
            )
    outcome_port.record_outcome(assessment_id, status, detail)
