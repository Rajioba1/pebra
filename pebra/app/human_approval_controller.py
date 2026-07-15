"""Trusted human approval flow for one pending, exact assessed candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pebra.app import (
    accept_risk_controller,
    assess_controller,
    candidate_apply_controller,
)
from pebra.core.constants import Decision, RiskMode
from pebra.ports.candidate_replay_port import CandidateReplayPort
from pebra.ports.store_port import StorePort


class HumanApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingApproval:
    assessment_id: str
    replay: candidate_apply_controller.CandidateReplay
    summary: dict[str, Any]


@dataclass(frozen=True)
class HumanApprovalOutcome:
    sanction_id: str
    reassessment_id: str
    changed_files: tuple[str, ...]


def _summary(
    assessment_id: str, replay: candidate_apply_controller.CandidateReplay
) -> dict[str, Any]:
    assessment = replay.assessment
    scores = assessment.get("scores") or {}
    packet = assessment.get("model_guidance_packet") or {}
    action = replay.request.candidate_actions[0]
    return {
        "assessment_id": assessment_id,
        "task": replay.request.task,
        "action_id": action.id,
        "files": list(action.expected_files),
        "risk_benefit": {
            key: scores.get(key)
            for key in ("expected_loss", "benefit", "expected_utility", "rau")
        },
        "reason": assessment.get("decision_reason")
        or "; ".join(str(value) for value in (packet.get("advisory") or {}).get("why", [])),
        "required_controls": list((packet.get("binding") or {}).get("required_controls") or []),
    }


def select_pending_approval(
    *,
    repo_id: str,
    assessed_commit: str,
    store: StorePort,
    replay_cache: CandidateReplayPort,
    assessment_id: str | None = None,
) -> PendingApproval:
    if not store.validate_chain():
        raise HumanApprovalError("assessment ledger integrity check failed")
    rows = store.pending_review_assessments(repo_id, assessed_commit)
    if assessment_id is not None:
        rows = [row for row in rows if row.get("assessment_id") == assessment_id]
        if not rows:
            raise HumanApprovalError(
                "the requested assessment is not pending human review at the current HEAD"
            )
    candidates_by_digest: dict[str, PendingApproval] = {}
    for row in rows:
        current_id = str(row.get("assessment_id") or "")
        try:
            replay = candidate_apply_controller.load_candidate_replay(
                current_id, repo_id, store, replay_cache
            )
        except candidate_apply_controller.CandidateApplyError:
            if assessment_id is not None:
                raise HumanApprovalError(
                    "the requested assessment no longer has an applicable candidate"
                ) from None
            continue
        digest = str(replay.metadata.get("digest") or current_id)
        candidates_by_digest.setdefault(
            digest, PendingApproval(current_id, replay, _summary(current_id, replay))
        )
    candidates = list(candidates_by_digest.values())
    if not candidates:
        raise HumanApprovalError("no candidate is pending human approval at the current HEAD")
    if len(candidates) > 1:
        ids = ", ".join(candidate.assessment_id for candidate in candidates)
        raise HumanApprovalError(
            f"multiple candidates are pending human approval ({ids}); use --assessment-id"
        )
    return candidates[0]


def _sanction_spec(pending: PendingApproval) -> dict[str, Any]:
    assessment = pending.replay.assessment
    packet = assessment.get("model_guidance_packet") or {}
    binding = (packet.get("binding") or {}).get("candidate")
    action_id = pending.replay.request.candidate_actions[0].id
    if not isinstance(binding, dict):
        raise HumanApprovalError("pending assessment has no exact candidate binding")
    return {
        "assessment_id": pending.assessment_id,
        "action_id": action_id,
        "risk_profile": {
            "assessment_id": pending.assessment_id,
            "action_id": action_id,
            "candidate_binding": binding,
        },
        "pre_edit_authorization_controls_satisfied": True,
        "pre_commit_required_controls": list(
            (packet.get("binding") or {}).get("required_controls") or []
        ),
        "high_risk_triggers": list(
            (packet.get("advisory") or {}).get("high_risk_triggers") or []
        ),
    }


def approve_and_apply(
    pending: PendingApproval,
    *,
    repo_id: str,
    repo_root: str,
    db_path: str,
    store: StorePort,
    assess_ports: dict[str, Any],
    application_ports: dict[str, Any],
) -> HumanApprovalOutcome:
    sanction_id = accept_risk_controller.accept_risk(
        repo_id,
        _sanction_spec(pending),
        sanction_port=assess_ports["sanction_port"],
    )
    replay = pending.replay
    try:
        reassessed = assess_controller.assess(
            replay.request,
            thresholds=replay.request.thresholds,
            start_path=repo_root,
            trusted_candidate_verification=replay.trusted_candidate_verification,
            trusted_task_obligations=replay.trusted_task_obligations,
            **assess_ports,
        )
        result = reassessed.recommended_result
        if not (
            result.recommended_decision is Decision.PROCEED
            and result.risk_mode is RiskMode.CONTROLLED_HIGH_RISK
        ):
            raise HumanApprovalError(
                "fresh candidate reassessment did not produce a controlled-high-risk proceed"
            )
        applied = candidate_apply_controller.apply_candidate(
            assessment_id=reassessed.assessment_id,
            repo_id=repo_id,
            repo_root=repo_root,
            db_path=db_path,
            store=store,
            **application_ports,
        )
    except Exception:
        store.invalidate_sanctions_for_assessment(
            pending.assessment_id, "human approval apply flow did not complete"
        )
        raise
    return HumanApprovalOutcome(
        sanction_id=sanction_id,
        reassessment_id=reassessed.assessment_id,
        changed_files=applied.changed_files,
    )
