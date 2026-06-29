"""Deterministic scorecard — a normalized JSON summary of one replay's calibration + safety metrics.

Every metric is DELEGATED to ``pebra.core.learning_eval.compute_promotion_metrics`` (which itself reuses
``prediction_error``). This module adds only: float normalization, JSON shaping, and the
genesis-vs-learned lift. It NEVER re-derives a metric — the scorecard must agree with the live engine by
construction.

Determinism target: ``same (prediction, outcome) pairs -> byte-identical scorecard.json`` (NOT the
SQLite DB, whose hash chain carries wall-clock timestamps). Floats are rounded to a fixed precision so
the artifact is platform-stable; undefined safety rates serialize as JSON ``null``.
"""

from __future__ import annotations

import json

from pebra.core import learning_eval as le

SCHEMA_VERSION = 1
PRECISION = 12  # decimals; normalizes the artifact so byte-identity is platform-stable


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, PRECISION)


def _lower_is_better_lift(genesis_value: float | None, learned_value: float | None) -> float | None:
    if genesis_value is None or learned_value is None:
        return None
    return _round(le.lift_lower_is_better(genesis_value, learned_value))


def build_scorecard(
    pairs: list[tuple[float, int]],
    outcomes: list[le.DecisionOutcome],
    *,
    label: str = "",
    n_bins: int = 10,
) -> dict:
    """Build the normalized scorecard for one replay track.

    ``pairs`` are (predicted_probability, actual_binary) for the proper scoring rules + ECE; ``outcomes``
    are the replayed decisions for the false-proceed / false-block safety rates.
    """
    m = le.compute_promotion_metrics(pairs, outcomes, n_bins=n_bins)
    return {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "n": m.n,
        "metrics": {
            "brier": _round(m.brier),
            "log_loss": _round(m.log_loss),
            "ece": _round(m.ece),
            "false_proceed_rate": _round(m.false_proceed_rate),
            "false_block_rate_c0_c2": _round(m.false_block_rate_c0_c2),
        },
    }


def compare_scorecards(genesis: dict, learned: dict) -> dict:
    """Genesis-vs-learned lift on calibration and safety rates.

    All reported lifts are lower-is-better: positive means the learned track improved. Undefined safety
    rates remain ``None`` because there is no honest denominator to compare.
    ``brier_improved`` keys off the primary proper scoring rule.
    """
    gm, lm = genesis["metrics"], learned["metrics"]
    lift = {
        "brier": _lower_is_better_lift(gm["brier"], lm["brier"]),
        "log_loss": _lower_is_better_lift(gm["log_loss"], lm["log_loss"]),
        "ece": _lower_is_better_lift(gm["ece"], lm["ece"]),
        "false_proceed_rate": _lower_is_better_lift(
            gm["false_proceed_rate"], lm["false_proceed_rate"]
        ),
        "false_block_rate_c0_c2": _lower_is_better_lift(
            gm["false_block_rate_c0_c2"], lm["false_block_rate_c0_c2"]
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "genesis_label": genesis["label"],
        "learned_label": learned["label"],
        "lift": lift,
        # Derived from the REPORTED (normalized) lift, not the raw subtraction, so the boolean can never
        # contradict the displayed number. The inputs are already 12-decimal scorecards, so a sub-1e-12
        # "improvement" is below the artifact's own resolution (and float-repr noise) — reporting it as
        # not-improved is the honest, internally-consistent call.
        "brier_improved": lift["brier"] > 0,
    }


def to_json(card: dict) -> str:
    """Deterministic serialization: sorted keys so the artifact is byte-identical across runs."""
    return json.dumps(card, sort_keys=True, indent=2)
