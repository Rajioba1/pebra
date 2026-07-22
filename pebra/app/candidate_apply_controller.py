"""Authorize and transactionally apply one cached, exact assessed candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pebra.core import candidate_parser, request_validator
from pebra.core.models import AssessmentRequest
from pebra.core.gate_contract import GatePermission, GateTier
from pebra.ports.candidate_application_port import (
    CandidateApplicationPort,
    CandidateGatePort,
)
from pebra.ports.candidate_replay_port import CandidateReplayPort
from pebra.ports.store_port import StorePort


class CandidateApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateApplyOutcome:
    assessment_id: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class CandidateReplay:
    assessment: dict[str, Any]
    request: AssessmentRequest
    trusted_candidate_verification: dict[str, Any] | None
    trusted_task_obligations: dict[str, Any] | None
    metadata: dict[str, Any]


def load_candidate_replay(
    assessment_id: str,
    repo_id: str,
    store: StorePort,
    replay_cache: CandidateReplayPort,
) -> CandidateReplay:
    if not store.validate_chain():
        raise CandidateApplyError("assessment ledger integrity check failed")
    try:
        assessment = store.load_assessment(assessment_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise CandidateApplyError("assessment could not be loaded") from exc
    if assessment.get("repo_id") != repo_id:
        raise CandidateApplyError("assessment belongs to a different repository")
    metadata = (assessment.get("request") or {}).get("candidate_replay")
    try:
        bundle = replay_cache.load(metadata)
        request = candidate_parser.parse(bundle["request"])
        request_validator.validate(request)
    except Exception as exc:  # noqa: BLE001 - malformed/tampered replay is never applicable
        raise CandidateApplyError("candidate replay could not be validated") from exc
    if len(request.candidate_actions) != 1:
        raise CandidateApplyError("candidate replay must contain exactly one action")
    patch = request.candidate_actions[0].proposed_patch
    if not isinstance(patch, str) or not patch.strip():
        raise CandidateApplyError("candidate replay does not contain an applicable patch")
    verification = bundle.get("trusted_candidate_verification")
    obligations = bundle.get("trusted_task_obligations")
    if verification is not None and not isinstance(verification, dict):
        raise CandidateApplyError("candidate replay verification sidecar is invalid")
    if obligations is not None and not isinstance(obligations, dict):
        raise CandidateApplyError("candidate replay obligations sidecar is invalid")
    return CandidateReplay(
        assessment=assessment,
        request=request,
        trusted_candidate_verification=verification,
        trusted_task_obligations=obligations,
        metadata=metadata,
    )


def apply_candidate(
    *,
    assessment_id: str,
    repo_id: str,
    repo_root: str,
    db_path: str,
    store: StorePort,
    replay_cache: CandidateReplayPort,
    gate: CandidateGatePort,
    applier: CandidateApplicationPort,
) -> CandidateApplyOutcome:
    replay = load_candidate_replay(assessment_id, repo_id, store, replay_cache)
    action = replay.request.candidate_actions[0]
    patch = action.proposed_patch or ""
    event = {
        "tool_name": "apply_patch",
        "cwd": repo_root,
        "tool_input": {"command": patch},
    }
    with applier.lock(repo_root):
        decision = gate.decide(
            event,
            db_path=db_path,
            consult_only=True,
            require_exact_match=True,
        )
        if not (
            decision.permission == GatePermission.CONTINUE
            and decision.tier == GateTier.CONSULTED
            and decision.matched_assessment_id == assessment_id
        ):
            raise CandidateApplyError(
                "exact assessment did not authorize candidate application"
            )
        try:
            replay_cache.consume(replay.metadata)
        except Exception as exc:  # noqa: BLE001 - consumed/missing replay is never reusable
            raise CandidateApplyError("candidate replay is no longer applicable") from exc
        changed = applier.apply(
            repo_root,
            patch,
            expected_files=tuple(action.expected_files),
            acquire_lock=False,
        )
    try:
        replay_cache.delete(replay.metadata)
    except Exception:  # noqa: BLE001 - cleanup failure cannot turn an applied edit into a failure
        pass
    return CandidateApplyOutcome(
        assessment_id=assessment_id,
        changed_files=tuple(changed),
    )
