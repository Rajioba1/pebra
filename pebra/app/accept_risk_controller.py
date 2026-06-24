"""accept_risk_controller (Architecture AD-26) — the controlled-high-risk authorization surface.

Creates a sanction event bound to the risk profile it approves. A sanction does not itself proceed:
the next assessment pre-fetches it into AssessmentInput, and gate 10 may convert a risk-threshold
ask_human/reject into a controlled-high-risk proceed — and it is invalidated on drift by pebra_verify.
Imports only core/ + ports/.
"""

from __future__ import annotations

from typing import Any

from pebra.ports.sanction_port import SanctionPort


def accept_risk(
    repo_id: str, sanction_spec: dict[str, Any], *, sanction_port: SanctionPort
) -> str:
    risk_profile = sanction_spec.get("risk_profile")
    if not risk_profile:
        raise ValueError("a sanction must be bound to a risk_profile (AD-26)")

    sanction = {
        "valid": True,
        "risk_profile": risk_profile,
        "assessment_id": sanction_spec.get("assessment_id"),
        "pre_edit_authorization_controls_satisfied": bool(
            sanction_spec.get("pre_edit_authorization_controls_satisfied", False)
        ),
        "converts_gates": list(sanction_spec.get("converts_gates", [2, 3, 4])),
        "pre_commit_required_controls": list(
            sanction_spec.get("pre_commit_required_controls", [])
        ),
        "high_risk_triggers": list(sanction_spec.get("high_risk_triggers", [])),
    }
    return sanction_port.create_sanction(repo_id, sanction)
