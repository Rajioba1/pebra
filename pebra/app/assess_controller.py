"""assess_controller (Architecture §11, plan §5) — the live assess use case.

The only orchestrator (plan §8): it validates the request, gathers evidence via ports, builds the
AssessmentInput IR, runs the pure engine (builder -> decision -> explanation -> guidance), and
persists through the store port. It imports only ``core/`` + ``ports/`` — never adapters.

Learning is NOT on this path in Phase 0 (no apply_snapshot; cold start). The engine never fetches:
everything it needs arrives inside AssessmentInput.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pebra.core import (
    assessment_builder,
    decision_engine,
    explanation_generator,
    model_guidance,
    prediction_capture,
    request_validator,
)
from pebra.core.apply_snapshot import apply_snapshot
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentInput, AssessmentRequest, AssessmentResult, CandidateAction
from pebra.ports.blast_radius_port import BlastRadiusProvider
from pebra.ports.evidence_port import EvidenceProvider
from pebra.ports.repository_registry_port import RepositoryRegistryPort
from pebra.ports.sanction_port import SanctionPort
from pebra.ports.snapshot_read_port import SnapshotReadPort
from pebra.ports.store_port import StorePort
from pebra.ports.structural_feature_port import StructuralFeatureProvider
from pebra.ports.symbol_diff_port import SymbolDiffProvider


@dataclass
class ScoredAction:
    action: CandidateAction
    result: AssessmentResult
    explanation: Explanation
    # Milestone 4a: the prediction manifest captured at scoring time (WHAT PEBRA predicted), persisted
    # atomically with the assessment. Shadow-only measurement — it never changes this decision.
    predictions: list[dict[str, Any]] = field(default_factory=list)


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

    # 3c — graph incompleteness caps evidence_quality: a blast estimate built over unresolved/dynamic/
    # wildcard imports (or missing expected files) is less trustworthy. The bounded penalty lowers
    # edit_confidence through the existing geometric mean (and can trip gate 8); a fully resolved
    # graph (score 0.0) leaves evidence_quality untouched, preserving the worked example.
    edit_confidence_factors = dict(evidence.edit_confidence_factors)
    if blast.graph_uncertainty_score > 0.0:
        supplied_eq = edit_confidence_factors.get("evidence_quality", 1.0)
        edit_confidence_factors["evidence_quality"] = max(
            0.0, supplied_eq - blast.graph_uncertainty_score
        )

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
        edit_confidence_factors=edit_confidence_factors,
        thresholds=effective_thresholds,
        policy_violations=list(evidence.policy_violations),
        repo_id=repo_id,
        repo_root=repo_root,
        p_success_variance=evidence.p_success_variance,
        review_cost_variance=evidence.review_cost_variance,
        variance_breakdown=evidence.variance_breakdown,
        benefit_delta_evidence=evidence.benefit_delta_evidence,
        symbol_diff_evidence=symbol_diff,
        blast_evidence=blast,
        architecture_evidence=evidence.architecture_evidence,
        # active_snapshot left at its default None here; M5c assigns it after apply_snapshot.
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
    # Phase-4 reframe: capture structural features pre-scoring and attach to the IR for CAPTURE only.
    # assessment_builder/decision_engine ignore inp.structural_features (no score/gate change — Hard
    # Rule). M5 apply_snapshot will later consume it pre-scoring. None provider -> empty features.
    sfp = ports.get("structural_feature_provider")
    if sfp is not None:
        inp.structural_features = sfp.build_features(inp)
    # M5c: apply the active learned snapshot PRE-scoring. The bundle is loaded once per assess()
    # (not once per action) and passed in here. apply_snapshot is pure; the assess path performs NO
    # learning write. No active facts -> identity (golden unchanged).
    # The prediction manifest below records the USED (possibly overridden) values; the raw priors are
    # preserved in inp.applied_snapshot_provenance.
    bundle = ports.get("active_snapshot_bundle")
    inp = apply_snapshot(inp, bundle)
    inp.active_snapshot = bundle
    assessment = assessment_builder.build_assessment(inp)
    policy_violations = inp.policy_violations
    result = decision_engine.decide(assessment, policy_violations=policy_violations)
    result.assessed_commit = ports.get("assessed_commit")
    explanation = explanation_generator.render(result, inp.thresholds)
    packet = model_guidance.render(result, action, explanation)
    result.model_guidance_packet = packet
    # Milestone 4a: capture the prediction manifest from the in-flight evidence (p_success and the
    # projected deltas are dropped from result.scores, so this is the only faithful record). Pure;
    # read-only; does not feed back into the decision (Hard Rule).
    manifest = prediction_capture.build_prediction_manifest(
        p_success=inp.p_success,
        events=inp.events,
        immediate_benefit=inp.immediate_benefit,
        projected_deltas=inp.benefit_delta_evidence.deltas,
        projected_benefit=result.scores["benefit"],
        action_id=action.id,
        features=inp.structural_features,
        applied_snapshot_provenance=inp.applied_snapshot_provenance,
    )
    return ScoredAction(
        action=action,
        result=result,
        explanation=explanation,
        predictions=[asdict(t) for t in manifest],
    )


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
    structural_feature_provider: StructuralFeatureProvider | None = None,
    snapshot_read_port: SnapshotReadPort | None = None,
) -> AssessmentOutcome:
    request_validator.validate(request)
    repo = repository_registry.resolve(start_path)
    active_snapshot_bundle = (
        snapshot_read_port.load_active_snapshot(repo.repo_id) if snapshot_read_port is not None else None
    )

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
                structural_feature_provider=structural_feature_provider,
                active_snapshot_bundle=active_snapshot_bundle,
            )
        )

    recommended = _recommended(scored)
    assessment_id = store.persist_assessment(
        recommended.result,
        {"task": request.task, "action_id": recommended.action.id},
        predictions=recommended.predictions,
    )
    return AssessmentOutcome(
        recommended_result=recommended.result,
        recommended_explanation=recommended.explanation,
        assessment_id=assessment_id,
        repo_id=repo.repo_id,
        repo_root=repo.repo_root,
        scored_actions=scored,
    )
