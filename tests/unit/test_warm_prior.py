from __future__ import annotations

from dataclasses import replace

import pytest

from pebra.core.constants import COLD_START_VARIANCES, LEARNED_VARIANCE_FLOOR_RATIO
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
        CalibratedPriorCell(action_type="edit", p_success=0.6, review_cost=0.3,
                            calibration_tag="edit", sample_size=100),
        CalibratedPriorCell(
            action_type="edit", language_tier="full", p_success=0.8,
            p_success_variance=0.006, p_success_aleatoric_variance=0.004,
            review_cost=0.1, review_cost_variance=0.001,
            review_cost_aleatoric_variance=0.001,
            calibration_tag="ts-edit", sample_size=60,
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
        action_type="edit", p_success=0.6, p_success_variance=0.015,
        p_success_aleatoric_variance=0.005, review_cost=0.2,
        review_cost_variance=0.006, review_cost_aleatoric_variance=0.004,
        calibration_tag="edit", sample_size=100,
    )
    out = apply_warm_prior(inp, (cell,))
    assert out.p_success == 0.9
    assert out.review_cost == 0.4
    assert out.p_success_variance == 0.02
    assert out.review_cost_variance == 0.01


def test_no_calibrated_cells_is_identity() -> None:
    inp = _input()
    assert apply_warm_prior(inp, ()) is inp


def test_warm_prior_resolves_each_field_from_its_best_matching_cell() -> None:
    cells = (
        CalibratedPriorCell(
            action_type="edit", p_success=0.7, p_success_variance=0.015,
            p_success_aleatoric_variance=0.005,
            calibration_tag="success", sample_size=100,
        ),
        CalibratedPriorCell(
            action_type="edit", language_tier="full", review_cost=0.08,
            review_cost_variance=0.002, review_cost_aleatoric_variance=0.001,
            calibration_tag="cost", sample_size=80,
        ),
    )
    out = apply_warm_prior(_input(), cells)
    assert out.p_success == pytest.approx(0.7)
    assert out.p_success_variance == pytest.approx(0.02)
    assert out.review_cost == pytest.approx(0.08)
    assert out.review_cost_variance == pytest.approx(0.003)
    assert out.warm_prior_provenance["field_sources"]["p_success"]["calibration_tag"] == "success"
    assert out.warm_prior_provenance["field_sources"]["review_cost"]["calibration_tag"] == "cost"


def test_unscoped_warm_prior_cell_is_rejected() -> None:
    inp = _input()
    cell = CalibratedPriorCell(p_success=0.99, calibration_tag="too-broad", sample_size=1000)
    assert apply_warm_prior(inp, (cell,)) is inp


def test_warm_prior_cell_keeps_per_target_aleatoric_variance() -> None:
    fields = CalibratedPriorCell.__dataclass_fields__
    assert "p_success_aleatoric_variance" in fields
    assert "review_cost_aleatoric_variance" in fields


def test_warm_prior_variance_is_floored_and_capped_per_target() -> None:
    cell = CalibratedPriorCell(
        action_type="edit",
        p_success_variance=0.0,
        p_success_aleatoric_variance=0.0,
        review_cost_variance=100.0,
        review_cost_aleatoric_variance=100.0,
        calibration_tag="bounded",
        sample_size=100,
    )

    out = apply_warm_prior(_input(), (cell,))

    assert out.p_success_variance == pytest.approx(
        COLD_START_VARIANCES["p_success"] * LEARNED_VARIANCE_FLOOR_RATIO
    )
    assert out.review_cost_variance == pytest.approx(COLD_START_VARIANCES["review_cost"])
    sources = out.warm_prior_provenance["field_sources"]
    assert sources["p_success_variance"]["applied_variance"] == out.p_success_variance
    assert sources["review_cost_variance"]["variance_cap"] == COLD_START_VARIANCES["review_cost"]


def test_warm_prior_variance_without_aleatoric_evidence_degrades_to_cold_cap() -> None:
    cell = CalibratedPriorCell(
        action_type="edit",
        p_success_variance=0.0,
        review_cost_variance=0.0,
        calibration_tag="legacy",
        sample_size=100,
    )

    out = apply_warm_prior(_input(), (cell,))

    assert out.p_success_variance == pytest.approx(COLD_START_VARIANCES["p_success"])
    assert out.review_cost_variance == pytest.approx(COLD_START_VARIANCES["review_cost"])
