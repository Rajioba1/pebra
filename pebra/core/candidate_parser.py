"""candidate_parser (Architecture §3.1, AD-8) — pure: raw request dict -> AssessmentRequest.

The surface reads JSON (or builds the single-action short form); this pure module maps it onto the
one canonical request object. It does no I/O and trusts nothing — ``request_validator`` enforces
well-formedness separately.
"""

from __future__ import annotations

from typing import Any

from pebra.core.models import SCHEMA_VERSION, AssessmentRequest, CandidateAction


def _parse_action(raw: dict[str, Any]) -> CandidateAction:
    return CandidateAction(
        id=str(raw.get("id", "")),
        label=str(raw.get("label", "")),
        action_type=str(raw.get("action_type", "edit")),
        proposed_patch=raw.get("proposed_patch"),
        affected_symbols=list(raw.get("affected_symbols", [])),
        expected_files=list(raw.get("expected_files", [])),
        is_dependency_change=bool(raw.get("is_dependency_change", False)),
        is_schema_change=bool(raw.get("is_schema_change", False)),
        is_migration=bool(raw.get("is_migration", False)),
    )


def parse(raw: dict[str, Any]) -> AssessmentRequest:
    actions = [_parse_action(a) for a in raw.get("candidate_actions", [])]
    return AssessmentRequest(
        task=str(raw.get("task", "")),
        candidate_actions=actions,
        evidence=dict(raw.get("evidence", {})),
        thresholds=dict(raw.get("thresholds", {})),
        schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
    )
