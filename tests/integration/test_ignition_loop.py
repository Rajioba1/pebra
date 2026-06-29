"""Phase 5 closure — IGNITION integration test.

The loop was never igniting: predictions insert shadow_mode=1, the production calibration views require
shadow_mode=0, and nothing flipped it -> load_production_calibration_rows always empty -> promotion
starved. This proves a COMPLETED assessment now yields production calibration rows (and a skipped one
does not), end-to-end through the real store, with the hash chain intact.
"""

from __future__ import annotations

from pebra.adapters.learning_store import LearningStore
from pebra.adapters.store.db import SqliteStore
from pebra.app import learning_controller as lc
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _result():
    return AssessmentResult(
        recommended_decision=Decision.PROCEED, requires_confirmation=False,
        action_status=ActionStatus.PENDING, risk_mode=RiskMode.NORMAL,
        scores={"benefit": 0.82}, repo_id="r", repo_root="/x",
    )


def _seed(store):
    return store.persist_assessment(
        _result(), {"task": "t", "action_id": "a1"},
        predictions=[{
            "action_id": "a1", "target_type": "risk_binary", "target_name": "p_success",
            "predicted_value": 0.74, "features": {},
        }],
    )


def test_completed_assessment_produces_production_calibration_rows(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.record_outcome(asm, "completed", {"actual_success": True})

    lc.measure_learning(asm, store=store, learning_port=LearningStore(store))

    rows = store.load_production_calibration_rows("r", "risk_binary")
    assert len(rows) >= 1                          # the loop finally ignites
    assert all(r["shadow_mode"] == 0 for r in rows)
    assert store.validate_chain() is True          # chain intact (no post-hoc flip)
    store.close()


def test_skipped_assessment_produces_no_production_rows(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    asm = _seed(store)
    store.record_outcome(asm, "skipped", {"actual_success": True})

    lc.measure_learning(asm, store=store, learning_port=LearningStore(store))

    assert store.load_production_calibration_rows("r", "risk_binary") == []
    assert store.validate_chain() is True
    store.close()
