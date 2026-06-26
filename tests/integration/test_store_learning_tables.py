"""Milestone 4d — prediction_errors + risk_snapshots: hash-chained shadow tables written by the
learning store."""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _seed(store) -> str:
    return store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED, requires_confirmation=False,
            action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
            scores={"benefit": 0.82}, repo_id="r", repo_root="/x",
        ),
        {"task": "t"},
    )


def _err_row(observed: bool) -> dict:
    if observed:
        return {"action_id": "a1", "target_type": "risk_binary", "target_name": "p_success",
                "predicted_probability": 0.74, "actual_outcome": 1, "residual": 0.26,
                "brier_error": 0.0676, "log_loss": 0.3011, "outcome_label_status": "observed"}
    return {"action_id": "a1", "target_type": "risk_binary", "target_name": "p_event.x",
            "predicted_probability": 0.1, "outcome_label_status": "censored"}


def test_prediction_error_insert_and_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    pe_id = store.insert_prediction_error(asm, _err_row(True))
    store.insert_prediction_error(asm, _err_row(False))
    assert pe_id.startswith("pe_")
    assert store.validate_chain() is True
    errs = store.load_prediction_errors("r")
    store.close()
    assert len(errs) == 2
    assert {e["outcome_label_status"] for e in errs} == {"observed", "censored"}


def test_tampered_prediction_error_breaks_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.insert_prediction_error(asm, _err_row(True))
    store._con.execute("UPDATE prediction_errors SET brier_error = 0.0 WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_insert_prediction_error_unknown_assessment_raises(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    with pytest.raises(KeyError):
        store.insert_prediction_error("asm_999", _err_row(True))
    store.close()


def test_risk_snapshot_insert_and_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    _seed(store)
    rs_id = store.insert_risk_snapshot("r", {"observed": 1, "censored": 0}, "shadow")
    assert rs_id.startswith("rs_")
    assert store.validate_chain() is True
    store.close()


def test_learning_measurement_rolls_back_rows_and_snapshot_on_failure(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    bad_row = {"action_id": "a1", "target_type": "risk_binary", "outcome_label_status": "observed"}

    with pytest.raises(KeyError):
        store.insert_learning_measurement(
            asm,
            [_err_row(True), bad_row],
            "r",
            {"assessment_id": asm, "observed": 1, "censored": 0},
            "shadow",
        )

    counts = store.chain_status()["counts"]
    assert counts["prediction_errors"] == 0
    assert counts["risk_snapshots"] == 0
    assert store.load_prediction_errors("r") == []
    assert store.prediction_errors_exist(asm) is False
    assert store.validate_chain() is True
    store.close()


def test_learning_measurement_success_keeps_both_chains_valid(tmp_path) -> None:
    # positive companion to the rollback test: the atomic multi-row path must keep the
    # prediction-error AND snapshot chains valid (a distinct helper from insert_prediction_error).
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    error_ids, snapshot_id = store.insert_learning_measurement(
        asm, [_err_row(True), _err_row(False)], "r",
        {"assessment_id": asm, "observed": 1, "censored": 1}, "shadow",
    )
    assert len(error_ids) == 2 and snapshot_id.startswith("rs_")
    counts = store.chain_status()["counts"]
    assert counts["prediction_errors"] == 2 and counts["risk_snapshots"] == 1
    assert store.prediction_errors_exist(asm) is True
    assert store.validate_chain() is True  # both chains intact after the atomic write
    store.close()


def test_chain_status_counts_learning_tables(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.insert_prediction_error(asm, _err_row(True))
    store.insert_risk_snapshot("r", {"observed": 1}, "shadow")
    counts = store.chain_status()["counts"]
    store.close()
    assert counts["prediction_errors"] == 1
    assert counts["risk_snapshots"] == 1
