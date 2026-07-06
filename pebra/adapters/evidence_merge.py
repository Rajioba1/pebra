"""evidence_merge (Slice 4d) — the evidence aggregation contract.

PURE composition: given the ALREADY-gathered pieces (the request base bundle + radon benefit deltas +
bandit security events + an evidence-quality penalty + config + architecture evidence), build ONE
merged ``EvidenceBundle`` without mutating the base. No radon/bandit/yaml imports here, so it stays
importable even in the dep-light env; the Slice-5 ``CompositeEvidenceProvider`` does the gathering and
calls this.

Contract:
  - request evidence is authoritative; radon only fills benefit deltas the request left projected/empty;
  - config can only RAISE criticality (never lower what the request claims);
  - request thresholds override config thresholds;
  - bandit events are appended, de-duplicated by event type against the request's events;
  - an evidence-quality penalty (bandit could not run) lowers evidence_quality, bounded at 0;
  - with no repo evidence (radon projected/empty, no bandit events, no penalty, default config,
    default architecture) the merged bundle EQUALS the request-only bundle — so wiring the composite
    never changes the worked example.
"""

from __future__ import annotations

from fnmatch import fnmatch

from pebra.core.constants import STAGE_MAP
from pebra.core.models import ArchitectureEvidence, BenefitDeltaEvidence, EvidenceBundle
from pebra.ports.config_port import PebraConfig


def _merge_benefit_delta_evidence(
    base: BenefitDeltaEvidence, radon: BenefitDeltaEvidence
) -> BenefitDeltaEvidence:
    # Request wins entirely: radon only fills a genuine gap — when the request benefit is projected
    # AND carries NO deltas of its own AND radon actually measured something. If the request supplied
    # its own (projected) deltas, they are authoritative and must not be relabeled as measured.
    if base.source_type != "projected" or base.deltas or not radon.deltas:
        return base
    merged = dict(radon.deltas)
    merged.update(base.deltas)  # any request-supplied key still wins
    return BenefitDeltaEvidence(
        scope=base.scope or radon.scope,
        source_type=radon.source_type,
        deltas=merged,
        future_change_exposure=base.future_change_exposure or radon.future_change_exposure,
    )


def _config_criticality(globs, affected_files: list[str]) -> str | None:
    """Highest criticality stage among config globs matching any affected file (None if no match)."""
    best: str | None = None
    for glob in globs:
        if any(fnmatch(f, glob.pattern) for f in affected_files):
            if best is None or STAGE_MAP.get(glob.stage, 0.0) > STAGE_MAP.get(best, 0.0):
                best = glob.stage
    return best


def _policy_violations(rules, affected_files: list[str]) -> list[str]:
    violations: list[str] = []
    for rule in rules:
        if any(fnmatch(f, rule.pattern) for f in affected_files):
            violations.append(rule.violation)
    return violations


def merge_evidence(
    base: EvidenceBundle,
    *,
    config: PebraConfig,
    architecture_evidence: ArchitectureEvidence,
    radon_benefit: BenefitDeltaEvidence,
    bandit_events: list[dict],
    evidence_quality_penalty: float,
    affected_files: list[str],
) -> EvidenceBundle:
    # criticality: config can only RAISE (never lower) the request's claimed stage.
    config_stage = _config_criticality(config.criticality_globs, affected_files)
    if config_stage and STAGE_MAP.get(config_stage, 0.0) > STAGE_MAP.get(base.criticality_stage, 0.0):
        stage, criticality_value = config_stage, STAGE_MAP.get(config_stage, base.criticality_value)
    else:
        stage, criticality_value = base.criticality_stage, base.criticality_value

    # copy each event dict (not just the list) so a later mutation of a merged event never reaches base.
    events = [dict(e) for e in base.events]
    seen = {e.get("event") for e in events}
    events.extend(dict(e) for e in bandit_events if e.get("event") not in seen)

    factors = dict(base.edit_confidence_factors)
    if evidence_quality_penalty > 0.0:
        factors["evidence_quality"] = max(
            0.0, factors.get("evidence_quality", 1.0) - evidence_quality_penalty
        )
    return EvidenceBundle(
        events=events,
        p_success=base.p_success,
        immediate_benefit=base.immediate_benefit,
        review_cost=base.review_cost,
        criticality_stage=stage,
        criticality_value=criticality_value,
        edit_confidence_factors=factors,
        thresholds={**config.thresholds, **base.thresholds},
        policy_violations=_policy_violations(config.policy_rules, affected_files),
        variance_breakdown=base.variance_breakdown,
        p_success_variance=base.p_success_variance,
        review_cost_variance=base.review_cost_variance,
        benefit_delta_evidence=_merge_benefit_delta_evidence(
            base.benefit_delta_evidence, radon_benefit
        ),
        architecture_evidence=architecture_evidence,
        candidate_verification=base.candidate_verification,
    )
