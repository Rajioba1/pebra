"""Step 5 Phase 4 — promotion controller orchestration (pure port DI; no adapter imports).

Loads production calibration rows, derives scope candidates from the features payload, runs the LOO
gate per candidate, and writes promoted facts via the LearningPort. Tested with fake ports.
"""

from __future__ import annotations

import json
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


def _features(
    *,
    action_type="edit",
    provider="cg-1.1.1",
    index="idx-7",
    is_public_api=False,
    domains=None,
    change_kind="BEHAVIORAL",
    graph_scope="scope-1",
):
    return {
        "symbol": {
            "action_type": action_type,
            "is_public_api": is_public_api,
            "change_kind": change_kind,
        },
        "structural": {"is_high_symbol_fan_in": False},
        "domain": {"matched_domains": domains or [], "criticality_stage": "C2"},
        "provenance": {
            "provider_version": provider,
            "index_version": index,
            "graph_scope_digest": graph_scope,
        },
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


def test_candidate_conditioned_graph_updates_are_never_promoted_globally():
    rows = [_row(0.1, 1, "p_event.public_api_break") for _ in range(5)]
    for row in rows:
        row["features"]["graph_refinement"] = {
            "status": "available", "fact_kinds": ["exported_binding_continuity"],
        }
    learning = _FakeLearning()

    result = pc.run_promotion(
        "r",
        store=_FakeStore(rows),
        learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=5),
    )

    assert result.promoted is False
    assert learning.calls == []


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


class _FakeStoreByType:
    def __init__(self, by_type):
        self._by = by_type

    def load_production_calibration_rows(self, repo_id=None, target_type="risk_binary"):
        return [dict(r) for r in self._by.get(target_type, [])]


def _bcont(pv, av, target_name="maintainability_delta.mi"):
    return {"predicted_value": pv, "actual_value": av, "target_type": "benefit_continuous",
            "target_name": target_name, "features": _features()}


def test_run_benefit_promotion_empty_no_write():
    learning = _FakeLearning()
    result = pc.run_benefit_promotion("r", store=_FakeStoreByType({}), learning_port=learning)
    assert result.promoted is False
    assert learning.calls == []


def test_run_benefit_promotion_continuous_writes_decoupled_snapshot():
    rows = [_bcont(0.0, 1.0) for _ in range(5)]  # model said 0, actual 1 -> learned mean helps (LOO-MSE)
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    result = pc.run_benefit_promotion(
        "r", store=_FakeStoreByType({"benefit_continuous": rows}), learning_port=learning, config=cfg
    )
    assert result.promoted is True
    _, metrics, facts, _ = learning.calls[0]
    assert metrics["promotion_reason"] == "M5d_benefit_promotion"
    assert any(f["target_type"] == "benefit_continuous" for f in facts)
    assert facts[0]["fact_json"]["calibration_method"] == "observed_mean_v1"


def test_run_review_cost_promotion_writes_decoupled_snapshot_with_variance():
    rows = [
        {"predicted_value": 0.8, "actual_value": 0.2, "target_type": "cost_continuous",
         "target_name": "review_cost", "features": _features()}
        for _ in range(5)
    ]
    learning = _FakeLearning()
    result = pc.run_review_cost_promotion(
        "r", store=_FakeStoreByType({"cost_continuous": rows}), learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=5),
    )
    assert result.promoted is True
    _, metrics, facts, _ = learning.calls[0]
    assert metrics["promotion_reason"] == "M5d_review_cost_promotion"
    assert facts[0]["target_type"] == "cost_continuous"
    assert facts[0]["target_name"] == "review_cost"
    assert facts[0]["fact_json"]["variance"] == pytest.approx(0.0)
    assert facts[0]["fact_json"]["aleatoric_variance"] == pytest.approx(0.0)
    assert facts[0]["fact_json"]["variance_method"] == "sample_mean_variance"


def test_risk_promotion_ignores_benefit_rows():
    # run_promotion loads RISK_BINARY only; benefit rows present but not promoted by the risk path.
    store = _FakeStoreByType({"benefit_continuous": [_bcont(0.0, 1.0) for _ in range(5)]})
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    result = pc.run_promotion("r", store=store, learning_port=_FakeLearning(), config=cfg)
    assert result.promoted is False
    assert "NO_CALIBRATION_ROWS" in result.veto_reasons


class _DriftStore:
    def __init__(self, active_value):
        self._rows = [{"predicted_probability": 0.5, "actual_outcome": 0, "target_type": "risk_binary",
                       "target_name": "p_success", "features": _features()} for _ in range(5)]
        self._active_value = active_value
        self.active_read = 0

    def load_production_calibration_rows(self, repo_id=None, target_type="risk_binary"):
        return [dict(r) for r in self._rows] if target_type == "risk_binary" else []

    def read_active_snapshot_rows(self, repo_id):
        self.active_read += 1
        return {"snapshot_id": "rs_old", "facts": [
            {"target_name": "p_success", "scope_kind": "global", "scope_value": "",
             "scope_json": "{}", "fact_json": json.dumps({"value": self._active_value})}
        ]}


def test_run_promotion_drift_freeze_prevents_write():
    # active fact says p_success=0.9 globally; ledger now says 0.0 -> drift 0.9 >= 0.2 -> freeze.
    store = _DriftStore(active_value=0.9)
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5, drift_freeze_threshold=0.2)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.frozen_due_to_drift is True
    assert result.promoted is False
    assert "DRIFT_FREEZE" in result.veto_reasons
    assert learning.calls == []  # frozen -> no write


def test_run_promotion_drift_below_threshold_proceeds_and_records():
    store = _DriftStore(active_value=0.05)  # close to ledger 0.0 -> drift 0.05 < 0.5
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5, drift_freeze_threshold=0.5)
    result = pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    assert result.promoted is True
    assert result.drift_score == pytest.approx(0.05)
    _, metrics, _, _ = learning.calls[0]
    assert metrics["drift_score"] == pytest.approx(0.05)


def test_run_promotion_drift_disabled_by_default_skips_active_read():
    store = _DriftStore(active_value=0.9)
    cfg = pe.PromotionConfig(min_calibration_samples=5)  # drift_freeze_threshold None
    result = pc.run_promotion("r", store=store, learning_port=_FakeLearning(), config=cfg)
    assert result.promoted is True
    assert store.active_read == 0  # the active snapshot must NOT be read when drift is disabled


def test_promoted_fact_carries_version_metadata():
    store = _FakeStore([_row(0.1, 1) for _ in range(5)])
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    pc.run_promotion("r", store=store, learning_port=learning, config=cfg)
    _, _, facts, _ = learning.calls[0]
    fj = facts[0]["fact_json"]
    assert fj["provider_version"] == "cg-1.1.1"
    assert fj["index_version"] == "idx-7"
    assert fj["graph_scope_digest"] == "scope-1"
    assert fj["calibration_method"] == "observed_rate_v1"
    assert fj["value"] == pytest.approx(1.0)
    assert fj["variance"] > 0.0
    assert fj["variance_method"] == "beta_1_1_parameter_variance"


def test_graph_derived_fact_requires_one_nonempty_scope_digest():
    rows = [_row(0.1, 1) for _ in range(5)]
    for row in rows:
        row["features"]["structural"]["is_high_symbol_fan_in"] = True
    learning = _FakeLearning()

    result = pc.run_promotion(
        "r",
        store=_FakeStore(rows),
        learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=5),
    )

    assert result.promoted is True
    facts = learning.calls[0][2]
    high_fanin = [fact for fact in facts if fact["scope_kind"] == "high_symbol_fan_in"]
    assert high_fanin
    assert high_fanin[0]["fact_json"]["graph_scope_digest"] == "scope-1"


def test_graph_derived_fact_uses_only_its_exact_group_for_provenance():
    rows = [_row(0.1, 1) for _ in range(8)]
    for index, row in enumerate(rows):
        is_high_fanin = index < 3
        row["features"]["structural"]["is_high_symbol_fan_in"] = is_high_fanin
        row["features"]["provenance"] = {
            "provider_version": "group-provider" if is_high_fanin else "outside-majority",
            "index_version": "group-index" if is_high_fanin else "outside-index",
            "graph_scope_digest": "group-scope" if is_high_fanin else "outside-scope",
        }
    learning = _FakeLearning()

    result = pc.run_promotion(
        "r",
        store=_FakeStore(rows),
        learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=3),
    )

    assert result.promoted is True
    facts = learning.calls[0][2]
    high_fanin = [fact for fact in facts if fact["scope_kind"] == "high_symbol_fan_in"]
    assert len(high_fanin) == 1
    provenance = high_fanin[0]["fact_json"]
    assert provenance["provider_version"] == "group-provider"
    assert provenance["index_version"] == "group-index"
    assert provenance["graph_scope_digest"] == "group-scope"


@pytest.mark.parametrize("scopes", [["scope-1", "scope-2"], [None, "scope-1"]])
def test_graph_derived_fact_vetoes_mixed_or_missing_scope_digests(scopes):
    rows = [_row(0.1, 1) for _ in range(6)]
    for index, row in enumerate(rows):
        row["features"]["structural"]["is_high_symbol_fan_in"] = True
        row["features"]["provenance"]["graph_scope_digest"] = scopes[index % len(scopes)]
    learning = _FakeLearning()

    result = pc.run_promotion(
        "r",
        store=_FakeStore(rows),
        learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=5),
    )

    assert result.promoted is True  # non-graph scopes retain their existing behavior
    facts = learning.calls[0][2]
    assert not any(fact["scope_kind"] == "high_symbol_fan_in" for fact in facts)
    assert "GRAPH_SCOPE_COHORT_MISMATCH" in result.veto_reasons


def test_legacy_rows_veto_only_graph_derived_facts():
    rows = [_row(0.1, 1) for _ in range(5)]
    for row in rows:
        row["features"]["structural"]["is_high_symbol_fan_in"] = True
        row["features"]["provenance"].pop("graph_scope_digest")
    learning = _FakeLearning()

    result = pc.run_promotion(
        "r",
        store=_FakeStore(rows),
        learning_port=learning,
        config=pe.PromotionConfig(min_calibration_samples=5),
    )

    assert result.promoted is True
    facts = learning.calls[0][2]
    assert any(fact["scope_kind"] == "global" for fact in facts)
    assert not any(fact["scope_kind"] == "high_symbol_fan_in" for fact in facts)
    assert "GRAPH_SCOPE_COHORT_MISMATCH" in result.veto_reasons


def test_derives_public_api_domain_and_domain_change_kind_scopes():
    rows = [
        {
            "predicted_probability": 0.1,
            "actual_outcome": 1,
            "target_type": "risk_binary",
            "target_name": "p_success",
            "features": _features(
                is_public_api=True,
                domains=["payments"],
                change_kind="CONTRACT",
            ),
        }
        for _ in range(5)
    ]
    learning = _FakeLearning()
    cfg = pe.PromotionConfig(min_calibration_samples=5)

    result = pc.run_promotion("r", store=_FakeStore(rows), learning_port=learning, config=cfg)

    assert result.promoted is True
    _, _, facts, _ = learning.calls[0]
    public_api_domain = [
        f for f in facts if f["scope_kind"] == "public_api_domain"
    ]
    domain_change_kind = [
        f for f in facts if f["scope_kind"] == "domain_change_kind"
    ]
    assert public_api_domain
    assert public_api_domain[0]["scope_json"] == {"domain": "payments"}
    assert domain_change_kind
    assert domain_change_kind[0]["scope_json"] == {
        "domain": "payments",
        "change_kind": "CONTRACT",
    }
