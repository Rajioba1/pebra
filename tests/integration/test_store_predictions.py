"""Milestone 4a — assessment_predictions: the immutable, hash-chained prediction manifest persisted
atomically with the assessment. Shadow-only (shadow_mode=1, label_status=pending) until an outcome
labels them."""

from __future__ import annotations

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result() -> AssessmentResult:
    return AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.82},
        repo_id="r",
        repo_root="/x",
        model_guidance_packet={"decision": "proceed"},
    )


def _manifest() -> list[dict]:
    return [
        {"target_type": "risk_binary", "target_name": "p_success", "predicted_value": 0.74,
         "action_id": "a1", "prediction_scope": "shadow", "provenance": {"provider": "pebra"}},
        {"target_type": "risk_binary", "target_name": "p_event.test_regression",
         "predicted_value": 0.10, "action_id": "a1", "prediction_scope": "shadow", "provenance": {}},
        {"target_type": "benefit_continuous", "target_name": "measured_benefit",
         "predicted_value": 0.82, "action_id": "a1", "prediction_scope": "shadow", "provenance": {}},
    ]


def test_persist_writes_prediction_manifest(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(_result(), {"task": "t"}, predictions=_manifest())
    rows = store.load_predictions(asm)
    store.close()
    by_name = {r["target_name"]: r for r in rows}
    assert set(by_name) == {"p_success", "p_event.test_regression", "measured_benefit"}
    assert by_name["p_success"]["predicted_value"] == 0.74
    assert by_name["p_success"]["label_status"] == "pending"   # no outcome yet
    assert by_name["p_success"]["shadow_mode"] == 1            # M4 shadow-only


def test_predictions_keep_chain_valid(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    store.persist_assessment(_result(), {"task": "t"}, predictions=_manifest())
    assert store.validate_chain() is True
    store.close()


def test_tampered_prediction_breaks_chain(tmp_path) -> None:
    db = str(tmp_path / "p.db")
    store = SqliteStore(db)
    store.persist_assessment(_result(), {"task": "t"}, predictions=_manifest())
    store._con.execute("UPDATE assessment_predictions SET predicted_value = 0.01 WHERE id = 1")
    assert store.validate_chain() is False
    store.close()


def test_chain_status_counts_predictions(tmp_path) -> None:
    store = SqliteStore(str(tmp_path / "p.db"))
    store.persist_assessment(_result(), {"task": "t"}, predictions=_manifest())
    counts = store.chain_status()["counts"]
    store.close()
    assert counts["assessment_predictions"] == 3


def test_persist_without_predictions_still_works(tmp_path) -> None:
    # backward compatible: the assess path that doesn't pass predictions is unaffected
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = store.persist_assessment(_result(), {"task": "t"})
    assert store.load_predictions(asm) == []
    assert store.validate_chain() is True
    store.close()
