"""Milestone 4a — prediction capture. Pure: assess-time predicted values -> immutable manifest.

The manifest is the first-class record of WHAT PEBRA predicted (p_success, per-event harm probs,
benefit). It is captured at assess time because the persisted result.scores drops p_success and the
projected maintainability deltas — computing them later would be reverse-engineering.
"""

from __future__ import annotations

from pebra.core import prediction_capture
from pebra.core.prediction_capture import build_prediction_manifest


def _worked_example_manifest():
    return build_prediction_manifest(
        p_success=0.74,
        events=[
            {"event": "test_regression", "p_event": 0.10},
            {"event": "public_api_break", "p_event": 0.03},
            {"event": "security_sensitive_change", "p_event": 0.04},
        ],
        immediate_benefit=0.82,
        review_cost=0.12,
        projected_deltas={},
        projected_benefit=0.82,
        action_id="a1",
    )


def test_manifest_captures_p_success_events_and_benefit_targets() -> None:
    manifest = _worked_example_manifest()
    by_name = {t.target_name: t for t in manifest}
    assert by_name["p_success"].target_type == "risk_binary"
    assert by_name["p_success"].predicted_value == 0.74
    assert by_name["p_event.test_regression"].target_type == "risk_binary"
    assert by_name["p_event.public_api_break"].predicted_value == 0.03
    assert by_name["immediate_benefit_realized"].target_type == "benefit_binary"
    assert by_name["measured_benefit"].target_type == "benefit_continuous"
    assert by_name["measured_benefit"].predicted_value == 0.82
    assert by_name["review_cost"].target_type == "cost_continuous"
    assert by_name["review_cost"].predicted_value == 0.12


def test_every_target_carries_action_id_and_shadow_scope() -> None:
    for t in _worked_example_manifest():
        assert t.action_id == "a1"
        assert t.prediction_scope == "shadow"  # M4 is shadow-only


def test_immediate_benefit_probability_is_clamped_to_unit_interval() -> None:
    manifest = build_prediction_manifest(
        p_success=0.5, events=[], immediate_benefit=1.4, projected_deltas={},
        projected_benefit=1.4, action_id="a1",
    )
    realized = next(t for t in manifest if t.target_name == "immediate_benefit_realized")
    assert realized.predicted_value == 1.0  # a benefit_binary target is a probability in [0,1]
    assert realized.provenance["source_type"] == "elicited_probability_proxy"
    assert realized.provenance["target_semantics"] == "immediate_benefit_clamped_to_probability_proxy"


def test_maintainability_deltas_captured_per_metric_when_present() -> None:
    manifest = build_prediction_manifest(
        p_success=0.7, events=[], immediate_benefit=0.5,
        projected_deltas={"complexity_delta": -2.0, "coupling_delta": 0.0},
        projected_benefit=0.5, action_id="a1",
    )
    by_name = {t.target_name: t for t in manifest}
    assert by_name["maintainability_delta.complexity_delta"].target_type == "benefit_continuous"
    assert by_name["maintainability_delta.complexity_delta"].predicted_value == -2.0
    assert "maintainability_delta.coupling_delta" in by_name


def test_features_attached_to_every_target_with_copy_semantics() -> None:
    feats = {"schema_version": 1, "symbol": {"is_public_api": True}}
    manifest = build_prediction_manifest(
        p_success=0.7, events=[{"event": "e", "p_event": 0.1}], immediate_benefit=0.5,
        projected_deltas={}, projected_benefit=0.5, action_id="a1", features=feats,
    )
    assert all(t.features == feats for t in manifest)
    feats["symbol"]["is_public_api"] = False  # mutate caller's dict after the call
    assert all(t.features["symbol"]["is_public_api"] is True for t in manifest)  # snapshot isolated


def test_features_default_empty_when_omitted() -> None:
    manifest = build_prediction_manifest(
        p_success=0.7, events=[], immediate_benefit=0.5, projected_deltas={},
        projected_benefit=0.5, action_id="a1",
    )
    assert all(t.features == {} for t in manifest)


def test_no_event_targets_when_no_events() -> None:
    manifest = build_prediction_manifest(
        p_success=0.7, events=[], immediate_benefit=0.5, projected_deltas={},
        projected_benefit=0.5, action_id="a1",
    )
    assert not any(t.target_name.startswith("p_event.") for t in manifest)


def test_manifest_tags_warm_prior_targets() -> None:
    manifest = build_prediction_manifest(
        p_success=0.7, events=[], immediate_benefit=0.0, projected_deltas={},
        projected_benefit=0.0, action_id="a", review_cost=0.1,
        warm_prior_provenance={
            "calibration_tag": "ts-edit", "sample_size": 60,
            "applied_fields": ["p_success", "review_cost"],
        },
    )
    by_name = {target.target_name: target for target in manifest}
    assert by_name["p_success"].provenance["warm_prior"]["calibration_tag"] == "ts-edit"
    assert by_name["review_cost"].provenance["warm_prior"]["sample_size"] == 60


def test_prediction_capture_exposes_prior_provenance_summary() -> None:
    assert hasattr(prediction_capture, "summarize_prior_provenance")


def test_prior_provenance_summary_reports_cold_start() -> None:
    summary = prediction_capture.summarize_prior_provenance(_worked_example_manifest())

    assert summary["source"] == "cold_start"
    assert summary["sources"] == ["cold_start"]
    assert summary["targets"]["p_success"]["source"] == "cold_start"


def test_variance_only_warm_prior_is_persisted_and_summarized() -> None:
    warm = {
        "calibration_tag": "ts-edit-v1",
        "sample_size": 120,
        "applied_fields": ["p_success_variance"],
        "field_sources": {
            "p_success_variance": {
                "calibration_tag": "ts-edit-v1",
                "applied_variance": 0.012,
                "variance_floor": 0.004,
                "variance_cap": 0.04,
            },
        },
    }
    manifest = build_prediction_manifest(
        p_success=0.7,
        events=[],
        immediate_benefit=0.0,
        projected_deltas={},
        projected_benefit=0.0,
        action_id="a",
        warm_prior_provenance=warm,
    )

    p_success = next(target for target in manifest if target.target_name == "p_success")
    assert p_success.provenance["warm_prior"] == warm
    summary = prediction_capture.summarize_prior_provenance(manifest)
    assert summary["source"] == "shipped"
    assert summary["calibration_tags"] == ["ts-edit-v1"]
    assert summary["targets"]["p_success"]["applied_variance"] == 0.012


def test_local_snapshot_has_precedence_and_retains_all_sources() -> None:
    manifest = build_prediction_manifest(
        p_success=0.8,
        events=[],
        immediate_benefit=0.0,
        projected_deltas={},
        projected_benefit=0.0,
        action_id="a",
        warm_prior_provenance={
            "calibration_tag": "population-v1",
            "sample_size": 100,
            "applied_fields": ["p_success"],
        },
        applied_snapshot_provenance={
            "snapshot_id": "rs_7",
            "applied_facts": [{
                "target": "p_success",
                "winning_fact_id": "lrf_9",
                "applied_variance": 0.008,
            }],
        },
    )

    summary = prediction_capture.summarize_prior_provenance(manifest)

    assert summary["source"] == "local_learned"
    assert summary["sources"] == ["local_learned", "shipped"]
    assert summary["snapshot_ids"] == ["rs_7"]
    assert summary["targets"]["p_success"]["source"] == "local_learned"
    assert summary["targets"]["p_success"]["applied_variance"] == 0.008


def test_warm_summary_attributes_sparse_fields_to_their_actual_cells() -> None:
    manifest = build_prediction_manifest(
        p_success=0.8,
        events=[],
        immediate_benefit=0.0,
        projected_deltas={},
        projected_benefit=0.0,
        review_cost=0.05,
        action_id="a",
        warm_prior_provenance={
            "calibration_tag": "review-v2",
            "sample_size": 200,
            "applied_fields": ["p_success", "p_success_variance", "review_cost"],
            "field_sources": {
                "p_success": {"calibration_tag": "success-v1"},
                "p_success_variance": {
                    "calibration_tag": "success-variance-v1",
                    "applied_variance": 0.008,
                },
                "review_cost": {"calibration_tag": "review-v2"},
            },
        },
    )

    summary = prediction_capture.summarize_prior_provenance(manifest)

    assert summary["calibration_tags"] == ["review-v2", "success-v1", "success-variance-v1"]
    assert summary["targets"]["p_success"]["calibration_tag"] == "success-v1"
    assert summary["targets"]["p_success"]["calibration_tags"] == [
        "success-v1", "success-variance-v1",
    ]
    assert summary["targets"]["review_cost"]["calibration_tag"] == "review-v2"
