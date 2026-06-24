"""Canonical constants & vocabulary (Architecture §4).

Pure stdlib only. This module is the single source of the decision enum, the stage map, the
consequence-bearing event set, scoring constants, and cold-start priors. Every value tagged
``prior_uncalibrated`` here is a documented default, not a fitted parameter.
"""

from __future__ import annotations

from enum import Enum

# --- Decision vocabulary (exactly 5 decisions; companions are NOT decisions) ---


class Decision(Enum):
    PROCEED = "proceed"
    INSPECT_FIRST = "inspect_first"
    TEST_FIRST = "test_first"
    ASK_HUMAN = "ask_human"
    REJECT = "reject"


class ActionStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class RiskMode(Enum):
    NORMAL = "normal"
    SENSITIVE_CONTEXT = "sensitive_context"
    ELEVATED_REVIEW = "elevated_review"
    CONTROLLED_HIGH_RISK = "controlled_high_risk"


class ChangeKind(Enum):
    COSMETIC = "COSMETIC"
    DIRECTIVE = "DIRECTIVE"
    TEST_ONLY = "TEST_ONLY"
    BEHAVIORAL = "BEHAVIORAL"
    CONTRACT = "CONTRACT"
    SIDE_EFFECT = "SIDE_EFFECT"
    UNKNOWN = "UNKNOWN"


# --- Stage map (spec §2.7): ordinal stage -> cardinal value. Raw stage is never multiplied. ---

STAGE_MAP: dict[str, float] = {
    "C0": 0.10,
    "C1": 0.30,
    "C2": 0.50,
    "C3": 0.80,
    "C4": 1.00,
}

# --- Criticality floor applies ONLY to these events (AD-1) ---

CONSEQUENCE_BEARING_EVENTS: frozenset[str] = frozenset(
    {
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
)

# --- Scoring constants ---

# Log-loss clip so confident-wrong predictions stay finite and deterministic.
LOG_LOSS_CLIP_EPS: float = 1e-15

# 90% lower-bound z-multiplier for the risk-adjusted utility (RAU = EU - z * utility_sd).
Z_ALPHA_90: float = 1.28

# edit_confidence is a weighted geometric mean over six factors, equally weighted (w = 1/6).
EDIT_CONFIDENCE_FACTORS: tuple[str, ...] = (
    "p_success",
    "evidence_quality",
    "testability",
    "reversibility",
    "source_reliability",
    "scope_control",
)
EDIT_CONFIDENCE_WEIGHT: float = 1.0 / len(EDIT_CONFIDENCE_FACTORS)

# --- Cold-start priors (AD-9) — documented uncalibrated defaults used when evidence is absent. ---

COLD_START_PRIORS: dict[str, object] = {
    "p_success": 0.50,
    "review_cost": 0.20,
    "criticality_stage": "C2",
    "edit_confidence_factors": {f: 0.50 for f in EDIT_CONFIDENCE_FACTORS},
}
