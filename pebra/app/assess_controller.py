"""assess_controller (Architecture §11, plan §5) — the live assess use case.

The only orchestrator (plan §8): it validates the request, gathers evidence via ports, builds the
AssessmentInput IR, runs the pure engine (builder -> decision -> explanation -> guidance), and
persists through the store port. It imports only ``core/`` + ``ports/`` — never adapters.

Learning is NOT on this path in Phase 0 (no apply_snapshot; cold start). The engine never fetches:
everything it needs arrives inside AssessmentInput.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pebra.core import (
    assessment_builder,
    decision_engine,
    explanation_generator,
    model_guidance,
    request_validator,
)
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentInput, AssessmentRequest, AssessmentResult, CandidateAction
from pebra.ports.blast_radius_port import BlastRadiusProvider
from pebra.ports.evidence_port import EvidenceProvider
from pebra.ports.repository_registry_port import RepositoryRegistryPort
from pebra.ports.sanction_port import SanctionPort
from pebra.ports.store_port import StorePort
from pebra.ports.symbol_diff_port import SymbolDiffProvider


@dataclass
class ScoredAction:
    action: CandidateAction
    result: AssessmentResult
    explanation: Explanation


@dataclass
class AssessmentOutcome:
    recommended_result: AssessmentResult
    recommended_explanation: Explanation
    assessment_id: str
    repo_id: str
    repo_root: str
    scored_actions: list[ScoredAction] = field(default_factory=list)


def _build_input(
    request: AssessmentRequest,
    action: CandidateAction,
    repo_id: str,
    repo_root: str,
    thresholds: dict[str, float],
    *,
    evidence_provider: EvidenceProvider,
    symbol_diff_provider: SymbolDiffProvider,
    blast_provider: BlastRadiusProvider,
    sanction_port: SanctionPort,
) -> AssessmentInput:
    evidence = evidence_provider.gather_evidence(request, action, repo_root)
    symbol_diff = symbol_diff_provider.symbol_diff(action, repo_root)
    blast = blast_provider.blast(action, repo_root)
    sanction = sanction_port.active_sanction(repo_id, action)

    effective_thresholds = {**evidence.thresholds, **thresholds}
    return AssessmentInput(
        request=request,
        action=action,
        events=evidence.events,
        p_success=evidence.p_success,
        immediate_benefit=evidence.immediate_benefit,
        review_cost=evidence.review_cost,
        criticality_stage=evidence.criticality_stage,
        criticality_value=evidence.criticality_value,
        edit_confidence_factors=evidence.edit_confidence_factors,
        thresholds=effective_thresholds,
        repo_id=repo_id,
        repo_root=repo_root,
        p_success_variance=evidence.p_success_variance,
        review_cost_variance=evidence.review_cost_variance,
        variance_breakdown=evidence.variance_breakdown,
        benefit_delta_evidence=evidence.benefit_delta_evidence,
        symbol_diff_evidence=symbol_diff,
        blast_evidence=blast,
        active_snapshot=None,  # no learning in Phase 0
        sanction=sanction,
    )


def _score_action(
    request: AssessmentRequest,
    action: CandidateAction,
    repo_id: str,
    repo_root: str,
    thresholds: dict[str, float],
    **ports: Any,
) -> ScoredAction:
    inp = _build_input(
        request, action, repo_id, repo_root, thresholds,
        evidence_provider=ports["evidence_provider"],
        symbol_diff_provider=ports["symbol_diff_provider"],
        blast_provider=ports["blast_provider"],
        sanction_port=ports["sanction_port"],
    )
    assessment = assessment_builder.build_assessment(inp)
    # Phase-0 stub: policy violations are read from the request evidence block. This is provisional —
    # in Phase 2 gate-1 will check a *configured* policy (yaml_config), not requester-supplied data,
    # so a requester cannot self-bypass gate-1 by omitting violations.
    policy_violations = request.evidence.get("policy_violations", [])
    result = decision_engine.decide(assessment, policy_violations=policy_violations)
    result.assessed_commit = ports.get("assessed_commit")
    explanation = explanation_generator.render(result, inp.thresholds)
    packet = model_guidance.render(result, action, explanation)
    result.model_guidance_packet = packet
    return ScoredAction(action=action, result=result, explanation=explanation)


def _recommended(scored: list[ScoredAction]) -> ScoredAction:
    """Pick the recommended action: best RAU among non-rejected, else the first."""
    from pebra.core.constants import Decision

    proceedable = [s for s in scored if s.result.recommended_decision is not Decision.REJECT]
    pool = proceedable or scored
    return max(pool, key=lambda s: s.result.scores["rau"])


def assess(
    request: AssessmentRequest,
    *,
    thresholds: dict[str, float],
    start_path: str,
    evidence_provider: EvidenceProvider,
    symbol_diff_provider: SymbolDiffProvider,
    blast_provider: BlastRadiusProvider,
    sanction_port: SanctionPort,
    repository_registry: RepositoryRegistryPort,
    store: StorePort,
    assessed_commit: str | None = None,
) -> AssessmentOutcome:
    request_validator.validate(request)
    repo = repository_registry.resolve(start_path)

    scored: list[ScoredAction] = []
    for action in request.candidate_actions:
        scored.append(
            _score_action(
                request, action, repo.repo_id, repo.repo_root, thresholds,
                evidence_provider=evidence_provider,
                symbol_diff_provider=symbol_diff_provider,
                blast_provider=blast_provider,
                sanction_port=sanction_port,
                assessed_commit=assessed_commit,
            )
        )

    recommended = _recommended(scored)
    assessment_id = store.persist_assessment(
        recommended.result, {"task": request.task, "action_id": recommended.action.id}
    )
    return AssessmentOutcome(
        recommended_result=recommended.result,
        recommended_explanation=recommended.explanation,
        assessment_id=assessment_id,
        repo_id=repo.repo_id,
        repo_root=repo.repo_root,
        scored_actions=scored,
    )
