"""request_validator (Architecture §3.1) — pure: structural validation of an AssessmentRequest.

Raises ``RequestValidationError`` on a malformed request. No I/O, no schema files (the surface may
additionally jsonschema-validate the raw payload; this guards the parsed object).
"""

from __future__ import annotations

from pebra.core.models import AssessmentRequest


class RequestValidationError(ValueError):
    """Raised when an AssessmentRequest is structurally invalid."""


def validate(request: AssessmentRequest) -> None:
    if not request.task or not request.task.strip():
        raise RequestValidationError("task must be a non-empty string")
    if not request.candidate_actions:
        raise RequestValidationError("at least one candidate action is required")

    seen: set[str] = set()
    for action in request.candidate_actions:
        if not action.id:
            raise RequestValidationError("every candidate action needs an id")
        if action.id in seen:
            raise RequestValidationError(f"duplicate candidate action id: {action.id!r}")
        seen.add(action.id)
        if not action.action_type:
            raise RequestValidationError(f"action {action.id!r} missing action_type")
