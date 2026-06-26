"""Milestone 4d — prediction_errors + risk_snapshots: hash-chained shadow tables written by the
learning store."""

from __future__ import annotations

import json

import pytest

from pebra.adapters.store.db import GENESIS, SqliteStore, _row_hash
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


@pytest.mark.parametrize("column,value", [
    ("shadow_mode", 0),
    ("guidance_packet_id", "'gp_forged'"),
    ("benefit_guidance_influenced", 1),
])
def test_tampering_m5_prediction_error_control_columns_breaks_chain(
    tmp_path, column, value
) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.insert_prediction_error(asm, _err_row(True))
    store._con.execute(f"UPDATE prediction_errors SET {column} = {value} WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_tampering_prediction_error_hash_version_breaks_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.insert_prediction_error(asm, _err_row(True))
    store._con.execute("UPDATE prediction_errors SET hash_version = 1 WHERE id = 1")
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


def test_tampering_m5_snapshot_lifecycle_columns_breaks_chain(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    _seed(store)
    store.insert_risk_snapshot("r", {"observed": 1, "censored": 0}, "shadow")
    store._con.execute("UPDATE risk_snapshots SET activated_at = 'forged' WHERE id = 1")
    assert store.validate_chain() is False
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
    assert counts["learned_risk_facts"] == 0


def test_m5_schema_columns_views_and_learned_fact_table_exist(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    con = store._con
    prediction_cols = {r[1] for r in con.execute("PRAGMA table_info(prediction_errors)")}
    snapshot_cols = {r[1] for r in con.execute("PRAGMA table_info(risk_snapshots)")}
    fact_cols = {r[1] for r in con.execute("PRAGMA table_info(learned_risk_facts)")}
    assert {"guidance_packet_id", "benefit_guidance_influenced", "shadow_mode"} <= prediction_cols
    assert {
        "parent_snapshot_id", "created_from_outcome_hash", "promotion_reason",
        "rollback_reason", "drift_score", "activated_at",
    } <= snapshot_cols
    assert {"scope_kind", "scope_value", "specificity_rank", "scope_json", "fact_json"} <= fact_cols
    fact_indexes = {
        r[1] for r in con.execute("PRAGMA index_list(learned_risk_facts)")
    }
    assert "ix_learned_risk_facts_apply_lookup" in fact_indexes
    assert con.execute("SELECT COUNT(*) FROM learned_risk_facts").fetchone()[0] == 0
    for view in (
        "risk_binary_calibration_data", "calibration_data",
        "benefit_binary_calibration_data", "benefit_continuous_calibration_data",
    ):
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?", (view,)
        ).fetchone()
    store.close()


def test_learned_risk_fact_flat_scope_fields_are_hash_chained(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    content = {
        "repo_id": "r",
        "snapshot_id": "rs_1",
        "fact_type": "measurement",
        "target_type": "risk_binary",
        "target_name": "p_event.security_sensitive_change",
        "scope_kind": "path_glob",
        "scope_value": "src/payments/**",
        "specificity_rank": 30,
        "scope": {"kind": "path_glob", "value": "src/payments/**"},
        "fact": {"calibrated_probability": 0.62},
        "status": "active",
        "requires_human_ratification": 0,
        "created_at": "2026-01-01T00:00:00Z",
    }
    content_json = json.dumps(content, sort_keys=True, separators=(",", ":"))
    row_hash = _row_hash(GENESIS, content_json)
    store._con.execute(
        """
        INSERT INTO learned_risk_facts (
            repo_id, snapshot_id, fact_type, target_type, target_name,
            scope_kind, scope_value, specificity_rank, scope_json, fact_json,
            status, requires_human_ratification, created_at, prev_hash, row_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content["repo_id"], content["snapshot_id"], content["fact_type"],
            content["target_type"], content["target_name"], content["scope_kind"],
            content["scope_value"], content["specificity_rank"],
            json.dumps(content["scope"], sort_keys=True, separators=(",", ":")),
            json.dumps(content["fact"], sort_keys=True, separators=(",", ":")),
            content["status"], content["requires_human_ratification"],
            content["created_at"], GENESIS, row_hash,
        ),
    )
    assert store.validate_chain() is True

    store._con.execute(
        "UPDATE learned_risk_facts SET scope_value = ? WHERE id = 1",
        ("src/billing/**",),
    )
    assert store.validate_chain() is False
    store.close()


def test_production_calibration_views_filter_shadow_guided_and_censored_rows(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    live = {**_err_row(True), "calibration_scope": "proceeded_edits_only", "shadow_mode": 0}
    store.insert_prediction_error(asm, live)
    store.insert_prediction_error(asm, {**live, "guidance_packet_id": "gp_1"})
    store.insert_prediction_error(asm, {**live, "shadow_mode": 1})
    store.insert_prediction_error(asm, {**_err_row(False), "calibration_scope": "proceeded_edits_only", "shadow_mode": 0})

    rows = store.load_production_calibration_rows("r", "risk_binary")
    store.close()
    assert len(rows) == 1
    assert rows[0]["guidance_packet_id"] is None
    assert rows[0]["shadow_mode"] == 0


def test_benefit_calibration_views_filter_guidance_influenced_rows(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    row = {
        "action_id": "a1",
        "target_type": "benefit_binary",
        "target_name": "immediate_benefit_realized",
        "predicted_probability": 0.7,
        "actual_outcome": 1,
        "residual": 0.3,
        "brier_error": 0.09,
        "log_loss": 0.3567,
        "outcome_label_status": "observed",
        "calibration_scope": "proceeded_edits_only",
        "shadow_mode": 0,
    }
    store.insert_prediction_error(asm, row)
    store.insert_prediction_error(asm, {**row, "benefit_guidance_influenced": True})
    rows = store.load_production_calibration_rows("r", "benefit_binary")
    store.close()
    assert len(rows) == 1
    assert rows[0]["benefit_guidance_influenced"] == 0
