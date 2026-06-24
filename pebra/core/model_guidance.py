"""model_guidance (Architecture §8, AD-23) — pure rendering of the pre-edit autonomy envelope.

This is NOT a second reasoning system and is not authored by an LLM. It is a deterministic rendering
of the approved action envelope, the selected decision, and the explanation/gate outputs. Binding
fields are enforced later by ``pebra_verify``; advisory fields steer the model without creating new
hard gates. Everything here must be reconstructable from canonical JSON.
"""

from __future__ import annotations

from typing import Any

from pebra.core import high_risk_controls
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult, CandidateAction

# Default risky-scope items: touching any of these invalidates the prior risk score (reassessment).
_DEFAULT_RISKY_SCOPE = [
    {"change": "public API changes", "action": "requires_reassessment"},
    {"change": "dependency upgrades", "action": "requires_reassessment"},
    {"change": "schema changes", "action": "requires_reassessment"},
]


def _safe_scope_files(action: CandidateAction) -> list[str]:
    files: list[str] = []
    files.extend(action.affected_symbols)
    for f in action.expected_files:
        if f not in files:
            files.append(f)
    return files


def render(
    result: AssessmentResult, action: CandidateAction, explanation: Explanation
) -> dict[str, Any]:
    selection = high_risk_controls.select_controls(result.high_risk_triggers)
    decision = result.recommended_decision.value

    required_checks: list[str] = []
    if decision in {"test_first", "proceed"} and action.expected_files:
        required_checks.append("run targeted tests for the touched scope before commit")

    return {
        "guidance_packet_id": f"gp_{action.id}",
        "decision": decision,
        "risk_mode": result.risk_mode.value,
        "binding": {
            "safe_scope": {
                "files": _safe_scope_files(action),
                "edit_policy": "smallest_sufficient_edit; no broad refactor",
            },
            "risky_scope": list(_DEFAULT_RISKY_SCOPE),
            "required_checks_before_commit": required_checks,
            "required_controls": selection.required_controls,
        },
        "advisory": {
            "high_risk_triggers": list(result.high_risk_triggers),
            "risk_facts": {
                "risk_level": explanation.risk_level_band,
                "affected_area": explanation.affected_area,
                "confidence": explanation.confidence_band,
            },
            "why": list(explanation.why),
            "suggested_inspection": [],
            "safer_alternative": (
                "make a targeted patch instead of a broad refactor"
                if decision != "proceed"
                else None
            ),
        },
        "provenance": {
            "safe_scope": "candidate action envelope",
            "risky_scope": "policy gates + detected risk events",
            "required_checks_before_commit": "test discovery + decision",
            "required_controls": "high_risk_triggers + control blueprint selector",
            "high_risk_triggers": "symbol_diff + criticality + gates",
            "risk_facts": "risk_report + evidence discovery",
            "why": "explanation_generator",
        },
    }
