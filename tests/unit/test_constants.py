"""Architecture §4 — canonical constants & vocabulary."""

from __future__ import annotations

import pytest

from pebra.core import constants as C


def test_stage_map_is_the_canonical_ordinal_to_cardinal_mapping() -> None:
    assert C.STAGE_MAP == {
        "C0": 0.10,
        "C1": 0.30,
        "C2": 0.50,
        "C3": 0.80,
        "C4": 1.00,
    }


def test_consequence_bearing_events_are_the_ten_named_events() -> None:
    assert C.CONSEQUENCE_BEARING_EVENTS == {
        "public_api_break",
        "security_sensitive_change",
        "external_state_damage",
        "migration_failure",
        "dependency_break",
        "api_contract_break",
        "route_behavior_break",
        "tool_schema_break",
        "response_shape_mismatch",
        "consumer_shape_mismatch",
    }


def test_log_loss_clip_eps() -> None:
    assert C.LOG_LOSS_CLIP_EPS == 1e-15


def test_z_alpha_90_is_the_90pct_lower_bound_multiplier() -> None:
    assert C.Z_ALPHA_90 == 1.28


def test_decision_enum_is_exactly_the_five_decisions() -> None:
    assert {d.value for d in C.Decision} == {
        "proceed",
        "inspect_first",
        "test_first",
        "ask_human",
        "reject",
    }


def test_action_status_values() -> None:
    assert {s.value for s in C.ActionStatus} == {
        "pending",
        "completed",
        "skipped",
        "rejected",
    }


def test_risk_mode_values() -> None:
    assert {m.value for m in C.RiskMode} == {
        "normal",
        "sensitive_context",
        "elevated_review",
        "controlled_high_risk",
    }


def test_change_kind_values() -> None:
    assert {k.value for k in C.ChangeKind} == {
        "COSMETIC",
        "DIRECTIVE",
        "TEST_ONLY",
        "BEHAVIORAL",
        "CONTRACT",
        "SIDE_EFFECT",
        "UNKNOWN",
    }


def test_cold_start_priors_exist_for_uncalibrated_fallback() -> None:
    # AD-9: cold-start priors live in constants, tagged prior_uncalibrated.
    priors = C.COLD_START_PRIORS
    assert 0.0 < priors["p_success"] <= 1.0
    assert priors["criticality_stage"] in C.STAGE_MAP
    assert set(priors["edit_confidence_factors"]) == set(C.EDIT_CONFIDENCE_FACTORS)


def test_edit_confidence_factors_are_six_equally_weighted() -> None:
    assert len(C.EDIT_CONFIDENCE_FACTORS) == 6
    assert C.EDIT_CONFIDENCE_FACTORS == (
        "p_success",
        "evidence_quality",
        "testability",
        "reversibility",
        "source_reliability",
        "scope_control",
    )
    assert C.EDIT_CONFIDENCE_WEIGHT == pytest.approx(1 / 6)
