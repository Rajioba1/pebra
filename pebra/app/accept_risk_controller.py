"""accept_risk_controller (Architecture AD-26) — the controlled-high-risk authorization surface.

Creates a sanction event bound to the risk profile it approves. A sanction does not itself proceed:
the next assessment pre-fetches it into AssessmentInput, and gate 10 may convert a risk-threshold
ask_human/reject into a controlled-high-risk proceed — and it is invalidated on drift by pebra_verify.
Imports only core/ + ports/.
"""

from __future__ import annotations

import re
from typing import Any

from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.human_review import SANCTION_CONVERTIBLE_GATES
from pebra.ports.sanction_port import SanctionPort

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _validated_binding(
    sanction_spec: dict[str, Any], risk_profile: Any
) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(risk_profile, dict):
        raise ValueError("a sanction risk_profile must be an exact candidate profile")
    assessment_id = sanction_spec.get("assessment_id")
    action_id = sanction_spec.get("action_id")
    if not isinstance(assessment_id, str) or not assessment_id:
        raise ValueError("a sanction must name its assessment_id")
    if not isinstance(action_id, str) or not action_id:
        raise ValueError("a sanction must name one action_id")
    if risk_profile.get("assessment_id") != assessment_id:
        raise ValueError("risk_profile assessment_id does not match the sanction")
    if risk_profile.get("action_id") != action_id:
        raise ValueError("risk_profile action_id does not match the sanction")
    candidate = risk_profile.get("candidate_binding")
    if not isinstance(candidate, dict) or candidate.get("algorithm") != CANDIDATE_BINDING_ALGORITHM:
        raise ValueError("a sanction must use the normalized-content candidate binding")
    files = candidate.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("a sanction candidate binding must contain files")
    if any(
        not isinstance(path, str)
        or not path
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        for path, digest in files.items()
    ):
        raise ValueError("a sanction candidate binding contains an invalid file digest")
    return assessment_id, action_id, candidate


def accept_risk(
    repo_id: str, sanction_spec: dict[str, Any], *, sanction_port: SanctionPort
) -> str:
    risk_profile = sanction_spec.get("risk_profile")
    if not risk_profile:
        raise ValueError("a sanction must be bound to a risk_profile (AD-26)")
    assessment_id, action_id, _candidate = _validated_binding(sanction_spec, risk_profile)

    sanction = {
        "valid": True,
        "risk_profile": risk_profile,
        "action_id": action_id,
        "action_ids": [action_id],
        "assessment_id": assessment_id,
        "pre_edit_authorization_controls_satisfied": bool(
            sanction_spec.get("pre_edit_authorization_controls_satisfied", False)
        ),
        "converts_gates": list(
            sanction_spec.get("converts_gates", sorted(SANCTION_CONVERTIBLE_GATES))
        ),
        "pre_commit_required_controls": list(
            sanction_spec.get("pre_commit_required_controls", [])
        ),
        "high_risk_triggers": list(sanction_spec.get("high_risk_triggers", [])),
    }
    return sanction_port.create_sanction(repo_id, sanction)
