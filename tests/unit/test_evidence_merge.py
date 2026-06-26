"""Slice 4d — the evidence aggregation contract (pure merge_evidence).

Proves: no-repo-evidence merges byte-identical to the request bundle (worked example unchanged when
the composite is wired in Slice 5); the merge never mutates the base; config raises (never lowers)
criticality; request thresholds override config; bandit events are deduped; radon fills only projected
benefit.
"""

from __future__ import annotations

from pebra.adapters.evidence_merge import merge_evidence
from pebra.core.models import ArchitectureEvidence, BenefitDeltaEvidence, EvidenceBundle
from pebra.ports.config_port import CriticalityGlob, PebraConfig


def _base(**overrides) -> EvidenceBundle:
    kwargs = dict(
        events=[],
        p_success=0.74,
        immediate_benefit=0.82,
        review_cost=0.12,
        criticality_stage="C2",
        criticality_value=0.50,
        edit_confidence_factors={"evidence_quality": 0.90, "p_success": 0.74},
        thresholds={"max_utility_sd_without_human": 0.20},
        benefit_delta_evidence=BenefitDeltaEvidence(source_type="projected", deltas={}),
    )
    kwargs.update(overrides)
    return EvidenceBundle(**kwargs)


def _empty_merge(base, **overrides):
    kwargs = dict(
        config=PebraConfig(),
        architecture_evidence=base.architecture_evidence,
        radon_benefit=BenefitDeltaEvidence(source_type="projected", deltas={}),
        bandit_events=[],
        evidence_quality_penalty=0.0,
        affected_files=["src/auth.py"],
    )
    kwargs.update(overrides)
    return merge_evidence(base, **kwargs)


def test_no_repo_evidence_merges_byte_identical_to_base() -> None:
    base = _base()
    assert _empty_merge(base) == base  # composite wiring must not change the worked example


def test_merge_does_not_mutate_base() -> None:
    base_events = [{"event": "x", "p_event": 0.1, "elicited_disutility": 0.2}]
    base_factors = {"evidence_quality": 0.9}
    base = _base(events=base_events, edit_confidence_factors=base_factors)
    _empty_merge(
        base,
        radon_benefit=BenefitDeltaEvidence(source_type="measured", deltas={"complexity_delta": -1.0}),
        bandit_events=[{"event": "security_sensitive_change", "p_event": 0.2, "elicited_disutility": 0.8}],
        evidence_quality_penalty=0.15,
    )
    assert base.events == [{"event": "x", "p_event": 0.1, "elicited_disutility": 0.2}]
    assert base.edit_confidence_factors == {"evidence_quality": 0.9}


def test_radon_fills_projected_benefit() -> None:
    base = _base()
    merged = _empty_merge(
        base, radon_benefit=BenefitDeltaEvidence(source_type="measured", deltas={"complexity_delta": -2.0})
    )
    assert merged.benefit_delta_evidence.source_type == "measured"
    assert merged.benefit_delta_evidence.deltas["complexity_delta"] == -2.0


def test_merge_event_dicts_are_not_aliased_to_base() -> None:
    base = _base(events=[{"event": "x", "p_event": 0.1, "elicited_disutility": 0.2}])
    merged = _empty_merge(
        base,
        bandit_events=[{"event": "security_sensitive_change", "p_event": 0.2, "elicited_disutility": 0.8}],
    )
    merged.events[0]["p_event"] = 0.99  # mutating a merged event must not touch base
    assert base.events[0]["p_event"] == 0.1


def test_request_projected_deltas_are_authoritative_over_radon() -> None:
    # the request supplied its own (projected) deltas -> radon must not overwrite or relabel them.
    base = _base(
        benefit_delta_evidence=BenefitDeltaEvidence(source_type="projected", deltas={"complexity_delta": 0.5})
    )
    merged = _empty_merge(
        base,
        radon_benefit=BenefitDeltaEvidence(
            source_type="measured", deltas={"complexity_delta": -2.0, "maintainability_index_delta": 3.0}
        ),
    )
    assert merged.benefit_delta_evidence == base.benefit_delta_evidence  # request wins entirely


def test_radon_does_not_override_non_projected_request_benefit() -> None:
    base = _base(
        benefit_delta_evidence=BenefitDeltaEvidence(source_type="measured", deltas={"complexity_delta": 0.5})
    )
    merged = _empty_merge(
        base, radon_benefit=BenefitDeltaEvidence(source_type="measured", deltas={"complexity_delta": -9.0})
    )
    assert merged.benefit_delta_evidence.deltas["complexity_delta"] == 0.5  # request wins


def test_config_raises_criticality() -> None:
    base = _base(criticality_stage="C2", criticality_value=0.50)
    merged = _empty_merge(
        base,
        config=PebraConfig(criticality_globs=[CriticalityGlob("src/payments/**", "C4")]),
        affected_files=["src/payments/charge.py"],
    )
    assert merged.criticality_stage == "C4"
    assert merged.criticality_value == 1.0


def test_config_never_lowers_criticality() -> None:
    base = _base(criticality_stage="C4", criticality_value=1.0)
    merged = _empty_merge(
        base,
        config=PebraConfig(criticality_globs=[CriticalityGlob("src/**", "C1")]),
        affected_files=["src/util.py"],
    )
    assert merged.criticality_stage == "C4"  # config glob is lower -> request stage kept


def test_request_thresholds_override_config() -> None:
    base = _base(thresholds={"k": 0.5})
    merged = _empty_merge(base, config=PebraConfig(thresholds={"k": 0.9, "j": 0.2}))
    assert merged.thresholds == {"k": 0.5, "j": 0.2}


def test_bandit_event_appended_and_deduped() -> None:
    sec = {"event": "security_sensitive_change", "p_event": 0.2, "elicited_disutility": 0.8}
    base = _base(events=[sec])
    merged = _empty_merge(base, bandit_events=[sec])
    assert sum(1 for e in merged.events if e["event"] == "security_sensitive_change") == 1


def test_bandit_penalty_lowers_evidence_quality() -> None:
    base = _base(edit_confidence_factors={"evidence_quality": 0.9})
    merged = _empty_merge(base, evidence_quality_penalty=0.15)
    assert merged.edit_confidence_factors["evidence_quality"] == 0.75


def test_architecture_evidence_is_carried_through() -> None:
    base = _base()
    arch = ArchitectureEvidence(god_node_score=0.9, cycle_participation=True)
    merged = _empty_merge(base, architecture_evidence=arch)
    assert merged.architecture_evidence.god_node_score == 0.9
    assert merged.architecture_evidence.cycle_participation is True
