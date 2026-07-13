"""request_validator (Architecture §3.1) — pure: structural validation of an AssessmentRequest.

Raises ``RequestValidationError`` on a malformed request. No I/O, no schema files (the surface may
additionally jsonschema-validate the raw payload; this guards the parsed object).
"""

from __future__ import annotations

from pebra.core.models import AssessmentRequest
from pebra.core.patch_paths import is_safe_repo_path, touched_files


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
        if action.proposed_patch:
            touched = {
                value.replace("\\", "/").removeprefix("./")
                for value in touched_files(action.proposed_patch)
            }
            if not touched:
                raise RequestValidationError(
                    f"action {action.id!r} proposed_patch must be a well-formed unified diff"
                )
            if any(not is_safe_repo_path(value) for value in touched):
                raise RequestValidationError(
                    f"action {action.id!r} proposed_patch paths must stay inside the repository"
                )
            expected = {
                value.replace("\\", "/").removeprefix("./")
                for value in action.expected_files
                if value
            }
            if any(not is_safe_repo_path(value) for value in expected):
                raise RequestValidationError(
                    f"action {action.id!r} expected_files must stay inside the repository"
                )
            if touched != expected:
                raise RequestValidationError(
                    f"action {action.id!r} expected_files must exactly match proposed_patch paths"
                )
