"""outcome_labels (Milestone 4b) — pure: the explicit learning-label contract for record-outcome.

A terminal ``action_status`` (completed/skipped/rejected) closes the lifecycle but is NOT a learning
label — "completed" is not the same as "safe" or "no regression". The fields below are the explicit,
optional labels an operator supplies in the outcome detail; calibration scores against THEM, and any
absent label is treated as ``censored`` (never guessed from lifecycle status).

Pure stdlib + core only. Validation here keeps malformed labels from reaching the store, and the same
extractor is the single source the learning controller reads.
"""

from __future__ import annotations

from typing import Any

# Recognized label fields. Free-form keys in the detail (e.g. "note") are ignored, not rejected.
_BOOL_LABELS = ("actual_success", "benefit_realized")
_COST_LABELS = ("actual_review_cost", "actual_rework_cost")
LABEL_KEYS = (*_BOOL_LABELS, *_COST_LABELS, "event_outcomes")


def validate_labels(detail: dict[str, Any] | None) -> None:
    """Raise ``ValueError`` if any recognized label is present with the wrong type. Absent labels are
    fine (they become ``censored`` downstream)."""
    if not detail:
        return
    for key in _BOOL_LABELS:
        if key in detail and not isinstance(detail[key], bool):
            raise ValueError(f"outcome label {key!r} must be a boolean")
    for key in _COST_LABELS:
        if key in detail:
            value = detail[key]
            # bool is a subclass of int — exclude it explicitly so True/False can't pose as a cost
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"outcome label {key!r} must be a number")
            if value < 0:
                raise ValueError(f"outcome label {key!r} must be non-negative")
    events = detail.get("event_outcomes")
    if events is not None:
        if not isinstance(events, dict):
            raise ValueError("outcome label 'event_outcomes' must be an object of event -> boolean")
        for event_name, observed in events.items():
            if not isinstance(observed, bool):
                raise ValueError(f"event_outcomes[{event_name!r}] must be a boolean")


def extract_labels(detail: dict[str, Any] | None) -> dict[str, Any]:
    """The recognized label subset of a (validated) detail dict — the single source the learning
    controller reads to decide which prediction targets have a real label vs. are censored."""
    if not detail:
        return {}
    labels: dict[str, Any] = {key: detail[key] for key in LABEL_KEYS if key in detail}
    return labels
