"""Step 5 Phase 4 — promotion controller orchestration (pure port DI; no adapter imports).

Loads production calibration rows, derives scope candidates from the features payload, runs the LOO
gate per candidate, and writes promoted facts via the LearningPort. Tested with fake ports.
"""

from __future__ import annotations

from typing import Any

import pytest

from pebra.app import promotion_controller as pc
from pebra.core import promotion_evaluator as pe


class _FakeStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def load_production_calibration_rows(self, repo_id=None, target_type="risk_binary"):
        return [dict(r) for r in self._rows]


class _FakeLearning:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def write_promotion(self, repo_id, snapshot_metrics, facts, snapshot_status="active"):
        self.calls.append((repo_id, snapshot_metrics, facts, snapshot_status))
        return "rs_1", [f"lrf_{i}" for i, _ in enumerate(facts)]


def _features(*, action_type="edit", provider="cg-1.1.1", index="idx-7"):
    return {
        "symbol": {"action_type": action_type, "is_public_api": False, "change_kind": "BEHAVIORAL"},
        "structural": {"is_high_symbol_fan_in": False},
        "domain": {"matched_domains": [], "criticality_stage": "C2"},
        "provenance": {"provider_version": provider, "index_version": index},
    }


def _row(p, y, target_name="p_success"):
    return {"predicted_probability": p, "actual_outcome": y, "target_type": "risk_binary",
            "target_name": target_name, "features": _features()}


def test_zero_rows_no_promotion():
    store = _FakeStore([])
    learning = _FakeLearning()
    result = pc.run_promotion("r", store=store, learning_port=learning)
    assert result.promoted is False
    assert result.veto_reasons == ["NO_CALIBRATION_ROWS"]
    assert learning.calls == []


def test_insufficient_n_all_vetoed_no_write():
    store = _FakeStore([_row(0.5, 1), _row(0.5, 0)])
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.promoted is False
    assert "INSUFFICIENT_N" in result.veto_reasons
    assert learning.calls == []


def test_clean_pass_writes_active_snapshot():
    # p_success, model said 0.1 but all succeeded -> genuinely predictive fact, no false-proceed veto.
    store = _FakeStore([_row(0.1, 1) for _ in range(5)])
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.promoted is True
    assert result.snapshot_id == "rs_1"
    assert len(learning.calls) == 1
    _, metrics, facts, status = learning.calls[0]
    assert status == "active"
    assert any(f["scope_kind"] == "global" for f in facts)


def test_delta_brier_negative_vetoed_no_write():
    store = _FakeStore([_row(0.5, 1), _row(0.5, 1), _row(0.5, 1), _row(0.5, 0)])
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=4)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.promoted is False
    assert learning.calls == []


def test_false_proceed_increase_vetoed_for_event_target():
    rows = ([_row(0.9, 0, "p_event.x") for _ in range(8)]
            + [_row(0.9, 1, "p_event.x") for _ in range(2)])
    store = _FakeStore(rows)
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.promoted is False
    assert "FALSE_PROCEED_RATE_INCREASE" in result.veto_reasons
    assert learning.calls == []


def test_promoted_fact_carries_version_metadata():
    store = _FakeStore([_row(0.1, 1) for _ in range(5)])
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    _, _, facts, _ = learning.calls[0]
    fj = facts[0]["fact_json"]
    assert fj["provider_version"] == "cg-1.1.1"
    assert fj["index_version"] == "idx-7"
    assert fj["calibration_method"] == "observed_rate_v1"
    assert fj["value"] == pytest.approx(1.0)
