"""request_evidence (Phase-0 EvidenceProvider) — reads request-supplied evidence + cold-start priors.

This is the Phase-0 ``EvidenceProvider`` implementation: the canonical request carries an
``evidence{}`` block (AD-8) with elicited/configured/measured values; this adapter passes them
through and fills any gaps with the cold-start priors from ``core.constants`` (AD-9). Later phases
replace it with radon/bandit/AST-derived evidence — the engine never sees the change.
"""

from __future__ import annotations

from pebra.core.constants import COLD_START_PRIORS, STAGE_MAP
from pebra.core.models import (
    AssessmentRequest,
    BenefitDeltaEvidence,
    CandidateAction,
    CandidateVerificationEvidence,
    EvidenceBundle,
)


class RequestEvidenceProvider:
    def gather_evidence(
        self, request: AssessmentRequest, action: CandidateAction, repo_root: str
    ) -> EvidenceBundle:
        ev = request.evidence
        stage = ev.get("criticality_stage", COLD_START_PRIORS["criticality_stage"])
        criticality_value = ev.get("criticality_value", STAGE_MAP.get(stage, 0.50))

        bde_raw = ev.get("benefit_delta_evidence", {"source_type": "projected"})
        benefit_delta = BenefitDeltaEvidence(
            scope=bde_raw.get("scope", ""),
            source_type=bde_raw.get("source_type", "projected"),
            deltas=dict(bde_raw.get("deltas", {})),
            future_change_exposure=bde_raw.get("future_change_exposure", 0.0),
        )
        verification_raw = ev.get("candidate_verification", {})
        verification = (
            CandidateVerificationEvidence(
                status=str(verification_raw.get("status", "not_applicable")),
                checks=dict(verification_raw.get("checks", {})),
                required_checks=[
                    str(check)
                    for check in verification_raw.get("required_checks", [])
                    if isinstance(check, str)
                ],
                domain=verification_raw.get("domain"),
                reason=verification_raw.get("reason"),
            )
            if isinstance(verification_raw, dict)
            else CandidateVerificationEvidence()
        )
        return EvidenceBundle(
            events=list(ev.get("events", [])),
            p_success=ev.get("p_success", COLD_START_PRIORS["p_success"]),
            immediate_benefit=ev.get("immediate_benefit", 0.0),
            review_cost=ev.get("review_cost", COLD_START_PRIORS["review_cost"]),
            criticality_stage=stage,
            criticality_value=criticality_value,
            edit_confidence_factors=dict(
                ev.get("edit_confidence_factors", COLD_START_PRIORS["edit_confidence_factors"])
            ),
            thresholds=dict(request.thresholds),
            variance_breakdown=ev.get("variance_breakdown"),
            p_success_variance=ev.get("p_success_variance", 0.0),
            review_cost_variance=ev.get("review_cost_variance", 0.0),
            benefit_delta_evidence=benefit_delta,
            candidate_verification=verification,
        )
