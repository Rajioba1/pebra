"""Architecture §5 — scoring math, pinned to the spec §10 worked example (validate_login patch).

These are the canonical numbers the Phase 0 milestone must reproduce:
    expected_loss = 0.10, expected_utility ≈ 0.3868 (0.39), utility_sd = 0.06,
    RAU = 0.31, edit_confidence ≈ 0.8338 (0.83), risk_budget_used = 0.50.
"""

from __future__ import annotations

import pytest

from pebra.core import score_math as sm


# --- criticality floor (AD-1): applies ONLY to consequence-bearing events ---


def test_floor_applies_to_consequence_bearing_event_when_stage_exceeds_elicited() -> None:
    # public_api_break is consequence-bearing; elicited 0.50 below C4 floor 1.00 -> floored up
    disutility, floor_applied = sm.apply_criticality_floor("public_api_break", 0.50, 1.00)
    assert disutility == 1.00
    assert floor_applied is True


def test_floor_not_applied_when_elicited_exceeds_stage() -> None:
    # security_sensitive_change elicited 0.90 above C3 floor 0.80 -> no floor
    disutility, floor_applied = sm.apply_criticality_floor("security_sensitive_change", 0.90, 0.80)
    assert disutility == 0.90
    assert floor_applied is False


def test_floor_never_applies_to_non_consequence_event() -> None:
    # test_regression is NOT in CONSEQUENCE_BEARING_EVENTS: elicited used as-is, no floor
    disutility, floor_applied = sm.apply_criticality_floor("test_regression", 0.40, 0.80)
    assert disutility == 0.40
    assert floor_applied is False


# --- expected_loss = Σ p_event_j · disutility_j ---


def test_expected_loss_reproduces_worked_example() -> None:
    events = [
        {"event": "test_regression", "p_event": 0.10, "disutility": 0.40},
        {"event": "public_api_break", "p_event": 0.03, "disutility": 0.80},
        {"event": "security_sensitive_change", "p_event": 0.04, "disutility": 0.90},
    ]
    total, components = sm.expected_loss(events)
    assert total == pytest.approx(0.10)
    assert components[0]["expected_loss"] == pytest.approx(0.04)
    assert components[1]["expected_loss"] == pytest.approx(0.024)
    assert components[2]["expected_loss"] == pytest.approx(0.036)


# --- expected_utility = p_success·benefit − expected_loss − review_cost ---


def test_expected_utility_reproduces_worked_example() -> None:
    eu = sm.expected_utility(p_success=0.74, benefit=0.82, expected_loss=0.10, review_cost=0.12)
    assert eu == pytest.approx(0.3868)
    assert round(eu, 2) == 0.39


# --- utility_sd = sqrt(Σ variance contribution terms) ---


def test_utility_sd_from_variance_breakdown() -> None:
    terms = {
        "p_success": 0.0016,
        "benefit": 0.0004,
        "event_losses": 0.0009,
        "review_cost": 0.0004,
        "scenario_variance": 0.0003,
    }
    sd = sm.utility_sd(terms)
    assert sd == pytest.approx(0.06)


# --- RAU = expected_utility − z·utility_sd (z = 1.28, 90% lower bound) ---


def test_risk_adjusted_utility_reproduces_worked_example() -> None:
    rau = sm.risk_adjusted_utility(expected_utility=0.3868, utility_sd=0.06)
    assert rau == pytest.approx(0.31)


# --- edit_confidence = weighted geometric mean over the six factors (w = 1/6) ---


def test_edit_confidence_reproduces_worked_example() -> None:
    factors = {
        "p_success": 0.74,
        "evidence_quality": 0.78,
        "testability": 0.80,
        "reversibility": 0.92,
        "source_reliability": 0.86,
        "scope_control": 0.92,
    }
    conf = sm.edit_confidence(factors)
    assert conf == pytest.approx(0.8338, abs=1e-4)
    assert round(conf, 2) == 0.83


def test_edit_confidence_equals_geometric_mean() -> None:
    factors = {k: 0.5 for k in ("p_success", "evidence_quality", "testability",
                                "reversibility", "source_reliability", "scope_control")}
    # geometric mean of identical values is the value itself
    assert sm.edit_confidence(factors) == pytest.approx(0.5)


# --- risk_budget_used = expected_loss / effective_threshold ---


def test_risk_budget_used_reproduces_worked_example() -> None:
    assert sm.risk_budget_used(expected_loss=0.10, effective_threshold=0.20) == pytest.approx(0.50)


def test_risk_budget_used_can_exceed_one_when_over_budget() -> None:
    assert sm.risk_budget_used(expected_loss=0.30, effective_threshold=0.20) == pytest.approx(1.5)


# --- blast_score = direct + 0.5·transitive (normalized to [0,1]) ---


def test_blast_score_combines_direct_and_half_transitive() -> None:
    # 2 direct + 0.5*4 transitive = 4 raw; normalization keeps it in [0,1]
    score = sm.blast_score(direct=2, transitive=4)
    assert 0.0 <= score <= 1.0


# --- fractional_rank: percentile of a value in an already-sorted distribution (incl. zeros).
# The core, stdlib home for the per-symbol fan-in percentile (mirrors import_graph_cache's
# file-level _fanin_percentiles math, lifted into core so the codegraph adapter can call it). ---


def test_fractional_rank_matches_bisect_right_over_len() -> None:
    # bisect_right([0,0,1,2,3,4,5], 3) == 5 ; 5/7
    assert sm.fractional_rank(3, [0, 0, 1, 2, 3, 4, 5]) == pytest.approx(5 / 7)


def test_fractional_rank_top_value_is_one() -> None:
    assert sm.fractional_rank(5, [0, 0, 1, 2, 3, 4, 5]) == pytest.approx(1.0)


def test_fractional_rank_zero_callers_counts_only_the_zero_bucket() -> None:
    # two zero-fan-in symbols out of seven -> a zero-fan-in symbol ranks at 2/7, not 0
    assert sm.fractional_rank(0, [0, 0, 1, 2, 3, 4, 5]) == pytest.approx(2 / 7)


def test_fractional_rank_empty_distribution_is_zero() -> None:
    assert sm.fractional_rank(0, []) == 0.0


def test_fractional_rank_value_above_all_is_one() -> None:
    assert sm.fractional_rank(99, [0, 1, 2]) == pytest.approx(1.0)
