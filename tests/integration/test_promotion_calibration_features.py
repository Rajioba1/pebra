"""Step 4 (B-lite) — production calibration rows must expose the persisted features_json so the M5d
promotion writer can derive symbol / public_api / domain / fan-in scopes (not just global/action_type).

The features are already captured in assessment_predictions; this only adds the read-side join keyed on
(assessment_id, target_type, target_name, action_id) with NULL-safe action_id matching.
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


_FEATURES = {
    # action_type lives INSIDE symbol (where every scope matcher reads it), not at top level.
    "symbol": {"symbol_id": "src/payments.py::charge", "is_public_api": True, "action_type": "edit"},
    "structural": {"symbol_fan_in_percentile": 0.96, "is_high_symbol_fan_in": True},
    "domain": {"matched_domains": ["payments"]},
}


def _result() -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=Decision.PROCEED, requires_confirmation=False,
        action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.82}, repo_id="r", repo_root="/x",
    )


def _production_err() -> dict:
    return {
        "action_id": "a1", "target_type": "risk_binary", "target_name": "p_success",
        "predicted_probability": 0.74, "actual_outcome": 1, "residual": 0.26,
        "brier_error": 0.0676, "log_loss": 0.3011, "outcome_label_status": "observed",
        "calibration_scope": "proceeded_edits_only", "shadow_mode": 0,
    }


def test_calibration_row_exposes_parsed_features(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"},
        predictions=[{
            "action_id": "a1", "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.74, "features": _FEATURES,
        }],
    )
    store.insert_prediction_error(asm, _production_err())

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()

    assert len(rows) == 1
    assert rows[0]["features"] == _FEATURES
    # the scope-deriving attributes M5d needs are reachable:
    assert rows[0]["features"]["structural"]["is_high_symbol_fan_in"] is True
    assert rows[0]["features"]["domain"]["matched_domains"] == ["payments"]


def test_calibration_row_features_default_empty_when_no_prediction(tmp_path) -> None:
    # a prediction_error with no matching assessment_prediction -> features is {} (never None/crash).
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(_result(), {"task": "t"})  # no predictions captured
    store.insert_prediction_error(asm, _production_err())

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()

    assert len(rows) == 1
    assert rows[0]["features"] == {}


def test_calibration_features_do_not_cross_action_ids(tmp_path) -> None:
    # features for action a2 must NOT attach to the a1 calibration row (NULL-safe, action-scoped join).
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"},
        predictions=[{
            "action_id": "a2", "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.74, "features": _FEATURES,
        }],
    )
    store.insert_prediction_error(asm, _production_err())  # action_id a1

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()

    assert len(rows) == 1
    assert rows[0]["features"] == {}  # a2's features must not leak onto a1's row


def test_calibration_features_join_null_action_id(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"},
        predictions=[{
            "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.74, "features": _FEATURES,
        }],
    )
    store.insert_prediction_error(asm, {**_production_err(), "action_id": None})

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()

    assert len(rows) == 1
    assert rows[0]["features"] == _FEATURES


def test_calibration_row_exposes_event_disutility_from_loss_components(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    result = AssessmentResult(
        recommended_decision=Decision.PROCEED, requires_confirmation=False,
        action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.82, "effective_threshold": 0.45,
                "loss_components": [{"event": "x", "p_event": 0.2, "disutility": 0.8,
                                     "expected_loss": 0.16}]},
        repo_id="r", repo_root="/x",
    )
    asm = store.persist_assessment(
        result, {"task": "t"},
        predictions=[{"action_id": "a1", "target_type": "risk_binary", "target_name": "p_event.x",
                      "predicted_value": 0.2, "features": _FEATURES}],
    )
    store.insert_prediction_error(asm, {
        "action_id": "a1", "target_type": "risk_binary", "target_name": "p_event.x",
        "predicted_probability": 0.2, "actual_outcome": 1, "residual": 0.8, "brier_error": 0.64,
        "log_loss": 1.6, "outcome_label_status": "observed",
        "calibration_scope": "proceeded_edits_only", "shadow_mode": 0,
    })

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()
    assert len(rows) == 1
    assert rows[0]["event_disutility"] == pytest.approx(0.8)


def test_calibration_row_event_disutility_none_for_non_event_target(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(_result(), {"task": "t"})  # scores has no loss_components
    store.insert_prediction_error(asm, _production_err())      # p_success target
    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()
    assert rows[0]["event_disutility"] is None


def test_malformed_calibration_features_default_empty(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"},
        predictions=[{
            "action_id": "a1", "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.74, "features": _FEATURES,
        }],
    )
    store._con.execute(
        "UPDATE assessment_predictions SET features_json = ? WHERE id = ?",
        ("{not-json", 1),
    )
    store.insert_prediction_error(asm, _production_err())

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()

    assert len(rows) == 1
    assert rows[0]["features"] == {}


def test_review_cost_calibration_row_is_available(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(
        _result(), {"task": "t"},
        predictions=[{
            "action_id": "a1", "target_type": "cost_continuous", "target_name": "review_cost",
            "predicted_value": 0.4, "features": _FEATURES,
        }],
    )
    store.insert_prediction_error(asm, {
        "action_id": "a1", "target_type": "cost_continuous", "target_name": "review_cost",
        "predicted_value": 0.4, "actual_value": 0.2, "residual": -0.2,
        "squared_error": 0.04, "outcome_label_status": "observed",
        "calibration_scope": "proceeded_edits_only", "shadow_mode": 0,
    })
    rows = store.load_production_calibration_rows("r", "cost_continuous")
    store.close()
    assert len(rows) == 1
    assert rows[0]["actual_value"] == pytest.approx(0.2)
    assert rows[0]["features"] == _FEATURES
