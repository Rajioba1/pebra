from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot
from pebra.core.calibrated_priors import CALIBRATED_PRIORS
from pebra.core.language_capability import LanguageCapability
from pebra.core.models import (
    AssessmentInput,
    AssessmentRequest,
    CandidateAction,
    CandidateGraphRiskEvidence,
    ScopedGraphRiskFact,
)
from pebra.core.warm_prior import apply_warm_prior
from pebra.core.warm_prior import CalibratedPriorCell
from pebra.app.assess_controller import _apply_prior_layers


def _input(*, tier: str = "full", explicit: dict[str, float] | None = None) -> AssessmentInput:
    patch = "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n@@ -1 +1 @@\n-old\n+new\n"
    request = AssessmentRequest(
        task="Preserve an exported binding",
        evidence=explicit or {},
        candidate_actions=[CandidateAction(
            id="a1",
            action_type="edit",
            label="Rename with compatibility alias",
            expected_files=["src/api.ts"],
            proposed_patch=patch,
        )],
    )
    signature_ratio = 1.0 if tier == "full" else 0.0
    return AssessmentInput(
        request=request,
        action=request.candidate_actions[0],
        events=[],
        p_success=float((explicit or {}).get("p_success", 0.5)),
        immediate_benefit=0.5,
        review_cost=0.2,
        criticality_stage="C2",
        criticality_value=0.5,
        edit_confidence_factors={},
        thresholds={},
        repo_id="repo",
        repo_root="/repo",
        language_capability=LanguageCapability(
            language="typescript",
            probe_status="measured",
            node_count=10,
            signature_coverage_ratio=signature_ratio,
            visibility_coverage_ratio=1.0,
        ),
        candidate_graph_risk_evidence=CandidateGraphRiskEvidence(
            status="available",
            verified_patch_hash=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            provider="materialized_codegraph",
            facts=(ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="graph_modify_risk",
                owner_node_ids=("owner",),
                confidence=1.0,
            ),),
        ),
    )


def test_provisional_prior_is_sparse_reviewed_and_cost_neutral() -> None:
    assert len(CALIBRATED_PRIORS) == 1
    (cell,) = CALIBRATED_PRIORS
    assert cell.calibration_tag == "zod_single_repo_provisional_v1"
    assert cell.sample_size == 3
    assert cell.action_type == "edit"
    assert cell.language == "typescript"
    assert cell.language_tier == "full"
    assert cell.graph_fact_kind == "exported_binding_continuity"
    assert cell.graph_event == "public_api_break"
    assert cell.graph_risk_source == "graph_modify_risk"
    assert cell.graph_provider == "materialized_codegraph"
    assert cell.min_graph_confidence == pytest.approx(0.90)
    assert cell.p_success == pytest.approx(0.8)
    assert cell.p_success_variance == pytest.approx(4 / 150)
    assert cell.p_success_aleatoric_variance == pytest.approx(2 / 15)
    assert cell.review_cost is None
    assert cell.review_cost_variance is None
    assert cell.review_cost_aleatoric_variance is None


def test_legacy_local_mean_does_not_inherit_shipped_variance() -> None:
    inp = replace(_input(), p_success_variance=0.04)
    tighter_shipped = CalibratedPriorCell(
        calibration_tag="future_tighter_prior",
        sample_size=100,
        action_type="edit",
        language="typescript",
        language_tier="full",
        graph_fact_kind="exported_binding_continuity",
        graph_event="public_api_break",
        graph_risk_source="graph_modify_risk",
        graph_provider="materialized_codegraph",
        min_graph_confidence=0.90,
        p_success=0.80,
        p_success_variance=0.001,
        p_success_aleatoric_variance=0.001,
    )
    local = SnapshotBundle(
        snapshot_id="legacy-local",
        facts=(SnapshotFact(
            fact_id="local-success",
            target_type="risk_binary",
            target_name="p_success",
            scope_kind="action_type",
            scope_value="edit",
            specificity_rank=1,
            value=0.92,
            sample_size=20,
            calibration_method="legacy_local_fit",
            variance=None,
            aleatoric_variance=None,
        ),),
    )

    applied = _apply_prior_layers(
        inp,
        calibrated_prior_cells=(tighter_shipped,),
        active_snapshot_bundle=local,
    )

    assert applied.p_success == pytest.approx(0.92)
    assert applied.p_success_variance == pytest.approx(0.04)


def test_provisional_prior_applies_only_to_matching_measured_graph_fact() -> None:
    matching = _input()
    applied = apply_warm_prior(matching, CALIBRATED_PRIORS)

    assert applied.p_success == pytest.approx(0.8)
    assert applied.p_success_variance == pytest.approx(0.04)
    assert applied.review_cost == matching.review_cost
    assert applied.warm_prior_provenance["calibration_tag"] == "zod_single_repo_provisional_v1"

    no_fact = replace(matching, candidate_graph_risk_evidence=CandidateGraphRiskEvidence())
    partial = _input(tier="partial")
    assert apply_warm_prior(no_fact, CALIBRATED_PRIORS) is no_fact
    assert apply_warm_prior(partial, CALIBRATED_PRIORS) is partial


@pytest.mark.parametrize("status", ["unavailable", "ambiguous", "not_applicable"])
def test_provisional_prior_rejects_non_authoritative_graph_evidence(status: str) -> None:
    inp = _input()
    evidence = replace(inp.candidate_graph_risk_evidence, status=status)
    candidate = replace(inp, candidate_graph_risk_evidence=evidence)

    assert apply_warm_prior(candidate, CALIBRATED_PRIORS) is candidate


def test_provisional_prior_rejects_hash_mismatch_low_confidence_and_other_language() -> None:
    inp = _input()
    mismatch = replace(
        inp,
        candidate_graph_risk_evidence=replace(
            inp.candidate_graph_risk_evidence, verified_patch_hash="0" * 64
        ),
    )
    low_confidence = replace(
        inp,
        candidate_graph_risk_evidence=replace(
            inp.candidate_graph_risk_evidence,
            facts=(replace(inp.candidate_graph_risk_evidence.facts[0], confidence=0.89),),
        ),
    )
    java = replace(
        inp,
        language_capability=replace(inp.language_capability, language="java"),
    )

    assert apply_warm_prior(mismatch, CALIBRATED_PRIORS) is mismatch
    assert apply_warm_prior(low_confidence, CALIBRATED_PRIORS) is low_confidence
    assert apply_warm_prior(java, CALIBRATED_PRIORS) is java


def test_explicit_request_and_repository_learning_beat_provisional_prior() -> None:
    explicit = _input(explicit={"p_success": 0.9})
    assert apply_warm_prior(explicit, CALIBRATED_PRIORS).p_success == pytest.approx(0.9)

    shipped = apply_warm_prior(_input(), CALIBRATED_PRIORS)
    learned = apply_snapshot(
        shipped,
        SnapshotBundle(
            snapshot_id="local",
            facts=(SnapshotFact(
                fact_id="local-success",
                target_type="risk_binary",
                target_name="p_success",
                scope_kind="action_type",
                scope_value="edit",
                specificity_rank=1,
                value=0.92,
                sample_size=20,
                calibration_method="local_outcome_fit",
                variance=0.01,
                aleatoric_variance=0.01,
            ),),
        ),
    )

    assert learned.p_success == pytest.approx(0.92)
    assert learned.applied_snapshot_provenance["snapshot_id"] == "local"
