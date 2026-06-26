"""Milestone 4a — prediction capture. Pure: assess-time predicted values -> immutable manifest.

The manifest is the first-class record of WHAT PEBRA predicted (p_success, per-event harm probs,
benefit). It is captured at assess time because the persisted result.scores drops p_success and the
projected maintainability deltas — computing them later would be reverse-engineering.
"""

from __future__ import annotations

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


def test_no_event_targets_when_no_events() -> None:
    manifest = build_prediction_manifest(
        p_success=0.7, events=[], immediate_benefit=0.5, projected_deltas={},
        projected_benefit=0.5, action_id="a1",
    )
    assert not any(t.target_name.startswith("p_event.") for t in manifest)
