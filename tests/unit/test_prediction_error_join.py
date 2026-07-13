"""Milestone 4d — pure prediction<->label join. Errors computed only where a real label exists;
everything else is 'censored' (never inferred from lifecycle status)."""

from __future__ import annotations

import pytest

from pebra.core import prediction_error as pe

_PREDS = [
    {"target_type": "risk_binary", "target_name": "p_success", "predicted_value": 0.74, "action_id": "a1"},
    {"target_type": "risk_binary", "target_name": "p_event.test_regression", "predicted_value": 0.10, "action_id": "a1"},
    {"target_type": "risk_binary", "target_name": "p_event.public_api_break", "predicted_value": 0.03, "action_id": "a1"},
    {"target_type": "benefit_binary", "target_name": "immediate_benefit_realized", "predicted_value": 0.82, "action_id": "a1"},
    {"target_type": "benefit_continuous", "target_name": "measured_benefit", "predicted_value": 0.82, "action_id": "a1"},
    {"target_type": "benefit_continuous", "target_name": "maintainability_delta.complexity_delta", "predicted_value": -2.0, "action_id": "a1"},
    {"target_type": "cost_continuous", "target_name": "review_cost", "predicted_value": 0.20, "action_id": "a1"},
]


def _by_name(rows):
    return {r["target_name"]: r for r in rows}


def test_observed_rows_compute_the_right_errors() -> None:
    labels = {
        "actual_success": True,
        "event_outcomes": {"test_regression": False},  # public_api_break unlabeled
        "benefit_realized": True,
        "actual_review_cost": 0.30,
    }
    rows = _by_name(
        pe.build_error_rows(_PREDS, labels, measured_benefit=0.5, measured_deltas={"complexity_delta": -1.5})
    )

    ps = rows["p_success"]
    assert ps["outcome_label_status"] == "observed"
    assert ps["actual_outcome"] == 1
    assert ps["predicted_probability"] == 0.74
    assert ps["brier_error"] == pytest.approx((0.74 - 1) ** 2)

    assert rows["p_event.test_regression"]["actual_outcome"] == 0  # event did not occur
    assert rows["immediate_benefit_realized"]["actual_outcome"] == 1

    mb = rows["measured_benefit"]
    assert mb["actual_value"] == 0.5
    assert mb["squared_error"] == pytest.approx((0.82 - 0.5) ** 2)
    assert rows["maintainability_delta.complexity_delta"]["actual_value"] == -1.5
    assert rows["review_cost"]["actual_value"] == pytest.approx(0.30)
    assert rows["review_cost"]["squared_error"] == pytest.approx(0.01)


def test_unlabeled_targets_are_censored_not_guessed() -> None:
    labels = {"actual_success": True}  # no event_outcomes, no benefit_realized
    rows = _by_name(pe.build_error_rows(_PREDS, labels))  # no benefit measurements
    assert rows["p_event.public_api_break"]["outcome_label_status"] == "censored"
    assert rows["p_event.test_regression"]["outcome_label_status"] == "censored"
    assert rows["immediate_benefit_realized"]["outcome_label_status"] == "censored"
    assert rows["measured_benefit"]["outcome_label_status"] == "censored"
    assert rows["review_cost"]["outcome_label_status"] == "censored"
    assert rows["p_success"]["outcome_label_status"] == "observed"  # the one real label


def test_empty_labels_censor_everything() -> None:
    rows = pe.build_error_rows(_PREDS, {})
    assert all(r["outcome_label_status"] == "censored" for r in rows)
    assert all(r.get("brier_error") is None and r.get("squared_error") is None for r in rows)
