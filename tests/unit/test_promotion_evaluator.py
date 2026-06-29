"""Step 5 Phase 1 — pure promotion evaluator (AD-18 §12.6).

compute_empirical_value = raw observed harmful rate (no shrinkage; the N-gate + replay are the noise
defense). evaluate_promotion_gate uses LEAVE-ONE-OUT replay so the delta_brier/delta_log_loss check is
genuinely out-of-sample (the in-sample mean is the Brier minimizer, so all-rows replay would be
self-fulfilling). The false-proceed veto applies ONLY to p_event.* targets (for p_success,
actual_outcome=1 is success, not harm).
"""

from __future__ import annotations

import pytest

from pebra.core import promotion_evaluator as pe


# --- compute_empirical_value -----------------------------------------------------------------

def _rows(outcomes):
    return [{"actual_outcome": y} for y in outcomes]


def test_empirical_value_is_raw_observed_rate():
    assert pe.compute_empirical_value(_rows([1, 1, 0, 0])) == pytest.approx(0.5)


def test_empirical_value_all_harmful():
    assert pe.compute_empirical_value(_rows([1, 1, 1])) == pytest.approx(1.0)


def test_empirical_value_all_safe():
    assert pe.compute_empirical_value(_rows([0, 0, 0])) == pytest.approx(0.0)


def test_empirical_value_empty_raises():
    with pytest.raises(ValueError):
        pe.compute_empirical_value([])


# --- _scope_matches_features -----------------------------------------------------------------

def _features(*, action_type="edit", symbol_id=None, is_public_api=False, domains=None,
             percentile=None, high_fan_in=False, change_kind="BEHAVIORAL", stage="C2"):
    return {
        "symbol": {
            "action_type": action_type, "change_kind": change_kind,
            "is_public_api": is_public_api, **({"symbol_id": symbol_id} if symbol_id else {}),
        },
        "structural": {
            **({"symbol_fan_in_percentile": percentile} if percentile is not None else {}),
            "is_high_symbol_fan_in": high_fan_in,
        },
        "domain": {"matched_domains": domains or [], "criticality_stage": stage},
    }


def test_scope_global_always_matches_even_empty_features():
    assert pe.scope_matches_features("global", "", {}, {}) is True


def test_scope_action_type():
    f = _features(action_type="refactor")
    assert pe.scope_matches_features("action_type", "refactor", {}, f) is True
    assert pe.scope_matches_features("action_type", "edit", {}, f) is False


def test_scope_symbol():
    f = _features(symbol_id="src/a.py::foo")
    assert pe.scope_matches_features("symbol", "src/a.py::foo", {}, f) is True
    assert pe.scope_matches_features("symbol", "src/a.py::bar", {}, f) is False


def test_scope_public_api():
    assert pe.scope_matches_features("public_api", "", {}, _features(is_public_api=True)) is True
    assert pe.scope_matches_features("public_api", "", {}, _features(is_public_api=False)) is False


def test_scope_domain():
    f = _features(domains=["payments", "auth"])
    assert pe.scope_matches_features("domain", "payments", {}, f) is True
    assert pe.scope_matches_features("domain", "billing", {}, f) is False


def test_scope_high_symbol_fan_in_by_flag_or_percentile():
    assert pe.scope_matches_features("high_symbol_fan_in", "", {}, _features(high_fan_in=True)) is True
    assert pe.scope_matches_features(
        "high_symbol_fan_in", "", {"min_percentile": 0.90}, _features(percentile=0.95)
    ) is True
    assert pe.scope_matches_features(
        "high_symbol_fan_in", "", {"min_percentile": 0.90}, _features(percentile=0.50)
    ) is False


def test_scope_domain_high_symbol_fan_in():
    f = _features(domains=["payments"], percentile=0.95)
    assert pe.scope_matches_features(
        "domain_high_symbol_fan_in", "", {"domain": "payments", "min_percentile": 0.90}, f
    ) is True
    assert pe.scope_matches_features(
        "domain_high_symbol_fan_in", "", {"domain": "auth", "min_percentile": 0.90}, f
    ) is False


def test_scope_empty_features_false_for_non_global():
    assert pe.scope_matches_features("symbol", "x", {}, {}) is False


def test_scope_unknown_kind_false():
    assert pe.scope_matches_features("nonsense", "x", {}, _features()) is False


# --- evaluate_promotion_gate (LOO replay) ----------------------------------------------------

def _crow(p, y, *, stage="C2"):
    return {"predicted_probability": p, "actual_outcome": y,
            "features": _features(stage=stage), "target_type": "risk_binary"}


def _cand(target_name="p_event.x", scope_kind="global", scope_value="", scope_json=None, value=0.5):
    return pe.CandidateFact(
        target_name=target_name, target_type="risk_binary", scope_kind=scope_kind,
        scope_value=scope_value, scope_json=scope_json or {}, value=value,
    )


def test_gate_vetoes_insufficient_n():
    rows = [_crow(0.5, 1), _crow(0.5, 0)]
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_promotion_gate(_cand(), rows, cfg)
    assert r.promoted is False
    assert r.veto_reason == "INSUFFICIENT_N"


def test_loo_vetoes_in_sample_only_improvement():
    # model predicts 0.5 for all; outcomes [1,1,1,0]. In-sample mean (0.75) *improves* Brier, but
    # leave-one-out it does NOT -> the gate must VETO. This is the whole point of LOO.
    rows = [_crow(0.5, 1), _crow(0.5, 1), _crow(0.5, 1), _crow(0.5, 0)]
    cfg = pe.PromotionConfig(min_calibration_samples=4)
    r = pe.evaluate_promotion_gate(_cand(), rows, cfg)
    assert r.promoted is False
    assert r.veto_reason == "DELTA_BRIER_NEGATIVE"
    assert r.delta_brier < 0


def test_genuinely_predictive_fact_is_promoted():
    # model said 0.1 (safe) but every outcome was harmful -> the learned ~1.0 fact genuinely helps.
    rows = [_crow(0.1, 1) for _ in range(5)]
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    r = pe.evaluate_promotion_gate(_cand(), rows, cfg)
    assert r.promoted is True
    assert r.veto_reason is None
    assert r.delta_brier > 0


def test_positive_delta_threshold_is_scored_on_matched_scope_not_diluted():
    matched = [
        {**_crow(0.1, 1), "features": _features(symbol_id="src/a.py::hot")}
        for _ in range(5)
    ]
    unrelated = [
        {**_crow(0.5, 0), "features": _features(symbol_id=f"src/a.py::cold{i}")}
        for i in range(100)
    ]
    cfg = pe.PromotionConfig(min_calibration_samples=5, min_delta_brier=0.5)
    candidate = _cand(scope_kind="symbol", scope_value="src/a.py::hot")
    r = pe.evaluate_promotion_gate(candidate, matched + unrelated, cfg)
    assert r.promoted is True
    assert r.n_group == 5
    assert r.n_eval == 105
    assert r.delta_brier > 0.5


# Shared fixture: 8 safe + 2 harmful, model over-predicts risk (0.9). The fact improves Brier overall
# but, on the harmful rows, drops the prediction below the decision threshold -> they would PROCEED.
def _fpr_fixture(stage="C2"):
    return [_crow(0.9, 0, stage=stage) for _ in range(8)] + [_crow(0.9, 1, stage=stage) for _ in range(2)]


def test_false_proceed_veto_fires_for_event_target():
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_event.x"), _fpr_fixture(), cfg)
    assert r.delta_brier > 0  # brier gate passes...
    assert r.promoted is False  # ...but false-proceed veto fires
    assert r.veto_reason == "FALSE_PROCEED_RATE_INCREASE"


def test_false_proceed_veto_NOT_applied_for_p_success():
    # Same numbers, but p_success: actual=1 is success, not harm -> the veto must not apply (else it
    # would invert the safety check). Brier/log-loss both improve, so it promotes.
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_success"), _fpr_fixture(), cfg)
    assert r.promoted is True
    assert r.false_proceed_rate_with is None  # not computed for p_success


def test_c4_weakening_detected_flag():
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_event.x"), _fpr_fixture(stage="C4"), cfg)
    assert r.promoted is False
    assert r.c4_weakening_detected is True


# --- benefit continuous gate (AD-29): LOO-MSE, no false-proceed/C4 -----------------------------

def _crow_cont(predicted_value, actual_value):
    return {"predicted_value": predicted_value, "actual_value": actual_value,
            "features": _features(), "target_type": "benefit_continuous"}


def _cand_cont(target_name="maintainability_delta.mi", scope_kind="global"):
    return pe.CandidateFact(target_name=target_name, target_type="benefit_continuous",
                            scope_kind=scope_kind, scope_value="", scope_json={})


def test_empirical_continuous_value_is_mean_actual():
    rows = [{"actual_value": 1.0}, {"actual_value": 0.0}, {"actual_value": 0.5}]
    assert pe.compute_empirical_continuous_value(rows) == pytest.approx(0.5)


def test_benefit_continuous_gate_promotes_when_loo_mse_improves():
    # model predicted 0.0 but actuals were all 1.0 -> learned ~1.0 mean predicts far better (LOO).
    rows = [_crow_cont(0.0, 1.0) for _ in range(5)]
    cfg = pe.PromotionConfig(min_calibration_samples=5)
    r = pe.evaluate_benefit_continuous_gate(_cand_cont(), rows, cfg)
    assert r.promoted is True
    assert r.delta_mse > 0
    assert r.false_proceed_rate_without is None  # continuous: no false-proceed veto


def test_benefit_continuous_gate_vetoes_when_loo_mse_worse():
    # perfect model (predicted == actual); the learned mean is worse out-of-sample on a varied set.
    rows = [_crow_cont(1.0, 1.0), _crow_cont(0.0, 0.0), _crow_cont(1.0, 1.0), _crow_cont(0.0, 0.0)]
    cfg = pe.PromotionConfig(min_calibration_samples=4)
    r = pe.evaluate_benefit_continuous_gate(_cand_cont(), rows, cfg)
    assert r.promoted is False
    assert r.veto_reason == "DELTA_MSE_NEGATIVE"


def test_benefit_continuous_gate_insufficient_n():
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_benefit_continuous_gate(_cand_cont(), [_crow_cont(0.0, 1.0)], cfg)
    assert r.promoted is False
    assert r.veto_reason == "INSUFFICIENT_N"


# --- disutility-aware false-proceed proxy (Item 5): (p * disutility) >= concern_budget -> concerned --

def _fpr_fixture_dis(disutility):
    rows = [_crow(0.9, 0) for _ in range(8)] + [_crow(0.9, 1) for _ in range(2)]
    for r in rows:
        r["event_disutility"] = disutility
    return rows


def test_event_concern_budget_default_is_prior():
    assert pe.PromotionConfig().event_concern_budget == pytest.approx(0.10)


def test_false_proceed_proxy_high_disutility_vetoes():
    # high-disutility event: harmful rows are "concerned" without the fact but the fact drops their
    # risk_contribution below the budget -> they flip to "not concerned" -> false-proceed increase.
    cfg = pe.PromotionConfig(min_calibration_samples=10, event_concern_budget=0.10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_event.x"), _fpr_fixture_dis(0.8), cfg)
    assert r.delta_brier > 0           # brier gate passes...
    assert r.promoted is False         # ...disutility-aware false-proceed veto fires
    assert r.veto_reason == "FALSE_PROCEED_RATE_INCREASE"


def test_false_proceed_proxy_low_disutility_no_veto():
    # same probabilities, low-disutility event: never crosses the concern budget -> no false-proceed veto.
    cfg = pe.PromotionConfig(min_calibration_samples=10, event_concern_budget=0.10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_event.x"), _fpr_fixture_dis(0.05), cfg)
    assert r.promoted is True


def test_false_proceed_proxy_falls_back_to_threshold_without_disutility():
    # rows lacking event_disutility -> the flat decision_threshold proxy is used (back-compat).
    cfg = pe.PromotionConfig(min_calibration_samples=10)
    r = pe.evaluate_promotion_gate(_cand(target_name="p_event.x"), _fpr_fixture(), cfg)
    assert r.veto_reason == "FALSE_PROCEED_RATE_INCREASE"
