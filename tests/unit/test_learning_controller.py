"""Milestone 4d — learning_controller.measure_learning over fake ports (no DB).

The controller is the sole shadow-measurement writer: it joins captured predictions to outcome
labels, computes errors where labels exist (else censored), and writes a shadow snapshot. It never
touches the assessment decision path.
"""

from __future__ import annotations

import pytest

from pebra.app import learning_controller as lc


class FakeStore:
    def __init__(self, *, predictions, outcomes, guardrails=None, repo_id="r", already_measured=False):
        self._predictions = predictions
        self._outcomes = outcomes
        self._guardrails = guardrails or []
        self._repo_id = repo_id
        self._already_measured = already_measured

    def load_predictions(self, assessment_id):
        return self._predictions

    def load_outcomes(self, assessment_id):
        return self._outcomes

    def assessment_detail(self, assessment_id):
        return {"content": {"repo_id": self._repo_id}, "guardrails": self._guardrails}

    def prediction_errors_exist(self, assessment_id):
        return self._already_measured


class FakeLearningPort:
    def __init__(self):
        self.rows = None
        self.snapshot = None

    def write_measurement(self, assessment_id, rows, repo_id, metrics, status="shadow"):
        self.rows = rows
        self.snapshot = (repo_id, metrics, status)
        return [f"pe_{i}" for i, _ in enumerate(rows, start=1)], "rs_1"


_PREDS = [
    {"target_type": "risk_binary", "target_name": "p_success", "predicted_value": 0.74, "action_id": "a1"},
    {"target_type": "risk_binary", "target_name": "p_event.test_regression", "predicted_value": 0.10, "action_id": "a1"},
    {"target_type": "benefit_continuous", "target_name": "measured_benefit", "predicted_value": 0.82, "action_id": "a1"},
]


def test_measure_writes_errors_and_shadow_snapshot() -> None:
    store = FakeStore(
        predictions=_PREDS,
        outcomes=[{
            "terminal_status": "completed",
            "detail": {"actual_success": True, "_pebra_label_source": "host"},
            "recorded_at": "t",
        }],
        guardrails=[{"measured_benefit": 0.5, "measured_benefit_deltas": {}}],
    )
    port = FakeLearningPort()
    result = lc.measure_learning("asm_1", store=store, learning_port=port)

    by_name = {r["target_name"]: r for r in port.rows}
    assert by_name["p_success"]["outcome_label_status"] == "observed"
    assert by_name["p_success"]["actual_outcome"] == 1
    assert by_name["measured_benefit"]["actual_value"] == 0.5
    assert by_name["p_event.test_regression"]["outcome_label_status"] == "censored"  # no event label

    assert port.snapshot[2] == "shadow"          # snapshot is shadow-only
    assert result.observed == 2 and result.censored == 1
    assert result.snapshot_id == "rs_1"
    assert len(result.prediction_error_ids) == 3


def test_completed_outcome_marks_rows_production_eligible() -> None:
    # IGNITION: a completed (proceeded) edit -> rows are production calibration (shadow_mode=0,
    # proceeded_edits_only) so load_production_calibration_rows finally returns them.
    store = FakeStore(
        predictions=_PREDS,
        outcomes=[{
            "terminal_status": "completed",
            "detail": {"actual_success": True, "_pebra_label_source": "host"},
            "recorded_at": "t",
        }],
    )
    port = FakeLearningPort()
    result = lc.measure_learning("asm_1", store=store, learning_port=port)
    assert all(r["shadow_mode"] == 0 for r in port.rows)
    assert all(r["calibration_scope"] == "proceeded_edits_only" for r in port.rows)
    assert result.proceeded is True


def test_skipped_outcome_keeps_rows_shadow() -> None:
    store = FakeStore(
        predictions=_PREDS,
        outcomes=[{"terminal_status": "skipped", "detail": {}, "recorded_at": "t"}],
    )
    port = FakeLearningPort()
    result = lc.measure_learning("asm_1", store=store, learning_port=port)
    assert all(r["shadow_mode"] == 1 for r in port.rows)
    assert all(r["calibration_scope"] == "shadow" for r in port.rows)
    assert result.proceeded is False


def test_rejected_outcome_keeps_rows_shadow() -> None:
    store = FakeStore(
        predictions=_PREDS,
        outcomes=[{"terminal_status": "rejected", "detail": {}, "recorded_at": "t"}],
    )
    port = FakeLearningPort()
    result = lc.measure_learning("asm_1", store=store, learning_port=port)
    assert all(r["shadow_mode"] == 1 for r in port.rows)
    assert result.proceeded is False


def test_measure_without_outcome_raises() -> None:
    store = FakeStore(predictions=_PREDS, outcomes=[])
    with pytest.raises(lc.OutcomeNotRecordedError):
        lc.measure_learning("asm_1", store=store, learning_port=FakeLearningPort())


def test_measure_without_predictions_raises() -> None:
    store = FakeStore(predictions=[], outcomes=[{"terminal_status": "completed", "detail": {}, "recorded_at": "t"}])
    with pytest.raises(ValueError):
        lc.measure_learning("asm_1", store=store, learning_port=FakeLearningPort())


def test_re_measure_is_refused_to_avoid_double_count() -> None:
    store = FakeStore(
        predictions=_PREDS,
        outcomes=[{"terminal_status": "completed", "detail": {"actual_success": True}, "recorded_at": "t"}],
        already_measured=True,
    )
    with pytest.raises(ValueError, match="already been measured"):
        lc.measure_learning("asm_1", store=store, learning_port=FakeLearningPort())
