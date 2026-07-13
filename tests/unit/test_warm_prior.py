from __future__ import annotations

from dataclasses import replace

from pebra.core.language_capability import LanguageCapability
from pebra.core.models import AssessmentInput, AssessmentRequest, CandidateAction
from pebra.core.warm_prior import CalibratedPriorCell, apply_warm_prior


def _input(*, explicit: dict | None = None) -> AssessmentInput:
    request = AssessmentRequest(
        task="t",
        evidence=explicit or {},
        candidate_actions=[CandidateAction(id="a", action_type="edit", label="edit")],
    )
    return AssessmentInput(
        request=request,
        action=request.candidate_actions[0],
        events=[],
        p_success=0.5,
        immediate_benefit=0.0,
        review_cost=0.2,
        criticality_stage="C2",
        criticality_value=0.5,
        edit_confidence_factors={},
        thresholds={},
        repo_id="r",
        repo_root="/r",
        language_capability=LanguageCapability(
            language="typescript", probe_status="measured", node_count=2,
            signature_coverage_ratio=1.0, visibility_coverage_ratio=1.0,
        ),
    )


def test_warm_prior_applies_most_specific_calibrated_cell() -> None:
    cells = (
        CalibratedPriorCell(p_success=0.6, review_cost=0.3, calibration_tag="global", sample_size=100),
        CalibratedPriorCell(
            action_type="edit", language_tier="full", p_success=0.8, p_success_variance=0.01,
            review_cost=0.1, review_cost_variance=0.002, calibration_tag="ts-edit", sample_size=60,
        ),
    )
    out = apply_warm_prior(_input(), cells)
    assert out.p_success == 0.8
    assert out.p_success_variance == 0.01
    assert out.review_cost == 0.1
    assert out.review_cost_variance == 0.002
    assert out.warm_prior_provenance["calibration_tag"] == "ts-edit"


def test_request_values_beat_warm_prior_but_missing_variance_can_be_filled() -> None:
    inp = replace(_input(explicit={"p_success": 0.9, "review_cost": 0.4}), p_success=0.9, review_cost=0.4)
    cell = CalibratedPriorCell(
        p_success=0.6, p_success_variance=0.02, review_cost=0.2,
        review_cost_variance=0.03, calibration_tag="global", sample_size=100,
    )
    out = apply_warm_prior(inp, (cell,))
    assert out.p_success == 0.9
    assert out.review_cost == 0.4
    assert out.p_success_variance == 0.02
    assert out.review_cost_variance == 0.03


def test_no_calibrated_cells_is_identity() -> None:
    inp = _input()
    assert apply_warm_prior(inp, ()) is inp
