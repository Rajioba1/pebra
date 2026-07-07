"""Canonical constants & vocabulary (Architecture §4).

Pure stdlib only. This module is the single source of the decision enum, the stage map, the
consequence-bearing event set, scoring constants, and cold-start priors. Every value tagged
``prior_uncalibrated`` here is a documented default, not a fitted parameter.
"""

from __future__ import annotations

from enum import Enum

# --- Decision vocabulary (companions are NOT decisions) ---


class Decision(Enum):
    PROCEED = "proceed"
    INSPECT_FIRST = "inspect_first"
    TEST_FIRST = "test_first"
    REVISE_SAFER = "revise_safer"
    ASK_HUMAN = "ask_human"
    REJECT = "reject"


class ActionStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    REJECTED = "rejected"


# Coarse graph classification tiers that prove an owner was TOUCHED but not what changed inside it.
# They stay UNCERTAIN — inheriting UNKNOWN's escalation, never suppressing it — at every risk/guidance
# gate, so a signature-unchanged result can never be read as behavior-unchanged. Single source of truth
# for the decision engine, the MODIFY risk model, and the model-guidance honesty note; keep them here so
# a future tier rename/addition can't desync the gates.
UNCERTAIN_STRUCTURE_TIERS: frozenset[str] = frozenset({"codegraph_structural", "codegraph_semantic"})


class RiskMode(Enum):
    NORMAL = "normal"
    SENSITIVE_CONTEXT = "sensitive_context"
    ELEVATED_REVIEW = "elevated_review"
    CONTROLLED_HIGH_RISK = "controlled_high_risk"


class GraphFreshness(Enum):
    """Trust state of the architecture map (AD-22). Decision-bearing: STALE (unresolved after a
    failed rebuild) routes to inspect_first; FRESH/REBUILT are trustworthy; UNKNOWN = not determined."""

    FRESH = "fresh"
    REBUILT = "rebuilt"
    STALE = "stale"
    UNKNOWN = "unknown"


class ChangeKind(Enum):
    COSMETIC = "COSMETIC"
    DIRECTIVE = "DIRECTIVE"
    TEST_ONLY = "TEST_ONLY"
    BEHAVIORAL = "BEHAVIORAL"
    CONTRACT = "CONTRACT"
    SIDE_EFFECT = "SIDE_EFFECT"
    UNKNOWN = "UNKNOWN"


class FileOperationKind(Enum):
    """File-level operation axis — ORTHOGONAL to ChangeKind (symbol-semantic change). A file can be
    deleted AND its symbols had a contract change; the two are tracked independently so a file op
    never masks the per-symbol severity. DELETE removes symbols (call-graph question); RENAME/MOVE
    relocate them (import-path question, modeled later via blast); CREATE adds a file (no callers)."""

    NONE = "NONE"
    DELETE = "DELETE"
    CREATE = "CREATE"
    RENAME = "RENAME"
    MOVE = "MOVE"


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

# AD-5 cold-start variance table. These are component variances, not a single opaque utility
# variance; score_normalizer composes them into utility variance with first-order propagation.
COLD_START_VARIANCES: dict[str, float] = {
    "p_success": 0.04,
    "benefit": 0.01,
    "p_event": 0.0025,
    "disutility": 0.0025,
    "review_cost": 0.01,
    "scenario_variance": 0.0003,
}

# --- Graph-incompleteness penalties (Slice 3c) — uncalibrated, bounded defaults. ---
# Each unresolved/dynamic/wildcard import (and missing expected file) erodes confidence in the blast
# estimate. Weights are per-count, summed, then capped at GRAPH_UNCERTAINTY_CAP: incompleteness can
# NUDGE a decision (lowering evidence_quality -> edit_confidence -> gate 8) but never collapse
# confidence to zero. External/stdlib imports are deliberately absent — they are not incompleteness.
# The repo_* weights capture dynamic/wildcard imports ELSEWHERE that could hide a dependent of the
# changed file (the reverse-direction blast risk); they are smaller than the edit-local weights.
GRAPH_UNCERTAINTY_CAP: float = 0.25
GRAPH_UNCERTAINTY_WEIGHTS: dict[str, float] = {
    "missing_file": 0.05,
    "parse_error_file": 0.08,
    "unresolved_import": 0.03,
    "dynamic_import": 0.02,
    "wildcard_import": 0.01,
    "repo_dynamic_import": 0.01,
    "repo_wildcard_import": 0.005,
}

# CodeGraph file metadata confidence penalties. Parse/index errors lower evidence quality; large or
# dense indexed files lower scope control. These are confidence-only signals, never expected-loss
# events, and are capped so metadata uncertainty nudges rather than dominates the decision.
CODEGRAPH_FILE_PARSE_ERROR_PENALTY: float = 0.08
CODEGRAPH_FILE_METADATA_SCOPE_PENALTY: float = 0.04
CODEGRAPH_LARGE_FILE_SIZE_BYTES: int = 200_000
CODEGRAPH_LARGE_FILE_NODE_COUNT: int = 500

# --- Architecture structural metrics (Slice 3f) — uncalibrated, repo-relative defaults. ---
# A file is a god-node "anchor" only if its fan-in (in-degree) meets BOTH a minimum floor AND ranks
# in the top fan-in percentile repo-wide. The floor prevents tiny-repo over-anchoring (a file imported
# once in a 3-file repo is not an architectural anchor); the percentile keeps it meaningful at scale.
ANCHOR_MIN_IN_DEGREE: int = 3
ANCHOR_FANIN_PERCENTILE: float = 0.90

# --- Learning (M5) calibration gate ---
# Minimum held-out outcome sample before a learned fact may OVERRIDE a live prediction. Shared by the
# M5c read-port (enforced as the primary >= gate) and M5d promotion (must agree) so a tiny-sample
# empirical rate never overrides a decision. apply_snapshot keeps a looser defense-in-depth gate
# (sample_size > 0 + a calibration_method) — the real floor lives here.
MIN_CALIBRATION_SAMPLES: int = 100

# --- Cold-start priors (AD-9) — documented uncalibrated defaults used when evidence is absent. ---

COLD_START_PRIORS: dict[str, object] = {
    "p_success": 0.50,
    "review_cost": 0.20,
    "criticality_stage": "C2",
    "edit_confidence_factors": {f: 0.50 for f in EDIT_CONFIDENCE_FACTORS},
}
