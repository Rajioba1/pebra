"""prediction_error (Milestone 4c, AD-15) — pure scoring-rule math, stdlib ``math`` only.

Turns a (predicted, actual) pair into a calibration error. Binary targets use Brier + log-loss;
continuous targets use squared error. ``residual`` is signed (``actual - predicted``; positive means
PEBRA under-predicted). numpy/sklearn are forbidden in ``core`` — these are hand-rolled.

This module only computes numbers from numbers. Which targets get which actual label, and which are
censored, is decided by the learning controller; promotion and active-snapshot read-back are separate
steps outside this module.
"""

from __future__ import annotations

import math
from typing import Any

from pebra.core.constants import LOG_LOSS_CLIP_EPS
from pebra.core.prediction_capture import (
    BENEFIT_BINARY,
    BENEFIT_CONTINUOUS,
    COST_CONTINUOUS,
    RISK_BINARY,
)


def brier_score(predicted: float, actual_binary: int) -> float:
    """(p - y)^2 for a binary outcome y in {0,1}; in [0,1]."""
    return (predicted - actual_binary) ** 2


def log_loss_single(predicted: float, actual_binary: int) -> float:
    """-(y·log p + (1-y)·log(1-p)), with p clipped to [eps, 1-eps] so a confident-and-wrong
    prediction is a large finite penalty, not -inf. The raw (unclipped) predicted value is what gets
    stored; clipping applies only inside the log."""
    p = min(1.0 - LOG_LOSS_CLIP_EPS, max(LOG_LOSS_CLIP_EPS, predicted))
    return -(actual_binary * math.log(p) + (1 - actual_binary) * math.log(1.0 - p))


def residual(predicted: float, actual: float) -> float:
    """Signed error: actual - predicted (positive = under-predicted)."""
    return actual - predicted


def squared_error(predicted: float, actual: float) -> float:
    """(actual - predicted)^2, for continuous targets."""
    return (actual - predicted) ** 2


def _require(pairs: list) -> None:
    if not pairs:
        raise ValueError("cannot aggregate an empty set of (predicted, actual) pairs")


def mean_brier(pairs: list[tuple[float, int]]) -> float:
    _require(pairs)
    return sum(brier_score(p, y) for p, y in pairs) / len(pairs)


def mean_log_loss(pairs: list[tuple[float, int]]) -> float:
    _require(pairs)
    return sum(log_loss_single(p, y) for p, y in pairs) / len(pairs)


def mse(pairs: list[tuple[float, float]]) -> float:
    _require(pairs)
    return sum(squared_error(p, a) for p, a in pairs) / len(pairs)


def signed_bias(pairs: list[tuple[float, float]]) -> float:
    """Mean signed residual — positive = PEBRA systematically under-predicts."""
    _require(pairs)
    return sum(residual(p, a) for p, a in pairs) / len(pairs)


# --- prediction <-> label join (Milestone 4d): pure mapping of captured predictions + outcome
# labels (+ measured benefit) into computed error rows. A target with no real label is 'censored';
# harm/success is never inferred from lifecycle status. No I/O, no decision feedback (Hard Rule).


def _risk_actual(target_name: str, labels: dict[str, Any]) -> int | None:
    """The actual binary for a risk target, or None when unlabeled (-> censored).

    p_success predicts P(edit succeeds): actual_success True -> 1.
    p_event.<e> predicts P(harmful event e occurs): event_outcomes[e] True -> 1.
    """
    if target_name == "p_success":
        value = labels.get("actual_success")
        return int(value) if isinstance(value, bool) else None
    if target_name.startswith("p_event."):
        event = target_name[len("p_event.") :]
        value = labels.get("event_outcomes", {}).get(event)
        return int(value) if isinstance(value, bool) else None
    return None


def _continuous_actual(
    target_name: str,
    labels: dict[str, Any],
    measured_benefit: float | None,
    measured_deltas: dict[str, float],
) -> float | None:
    """The actual continuous value for a benefit target, or None when unmeasured (-> censored)."""
    if target_name == "measured_benefit":
        return measured_benefit
    if target_name.startswith("maintainability_delta."):
        metric = target_name[len("maintainability_delta.") :]
        return measured_deltas.get(metric)
    if target_name == "review_cost":
        value = labels.get("actual_review_cost")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return None


def summarize_errors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate computed error rows into a calibration summary, keeping the three target types
    SEPARATE (AD-29: never mix a Brier score with an MSE). Only ``observed`` rows enter the metrics;
    a type with no observed rows reports ``pending_min_n``."""
    observed = [r for r in rows if r["outcome_label_status"] == "observed"]

    def _binary(target_type: str) -> dict[str, Any]:
        pairs = [
            (r["predicted_probability"], r["actual_outcome"])
            for r in observed
            if r["target_type"] == target_type
        ]
        if not pairs:
            return {"status": "pending_min_n", "n": 0}
        return {
            "status": "ok",
            "n": len(pairs),
            "brier": mean_brier(pairs),
            "log_loss": mean_log_loss(pairs),
            "bias": signed_bias([(p, float(y)) for p, y in pairs]),
        }

    cont_pairs = [
        (r["predicted_value"], r["actual_value"])
        for r in observed
        if r["target_type"] == BENEFIT_CONTINUOUS
    ]
    continuous = (
        {"status": "pending_min_n", "n": 0}
        if not cont_pairs
        else {"status": "ok", "n": len(cont_pairs), "mse": mse(cont_pairs),
              "bias": signed_bias(cont_pairs)}
    )
    cost_pairs = [
        (r["predicted_value"], r["actual_value"])
        for r in observed
        if r["target_type"] == COST_CONTINUOUS
    ]
    cost_continuous = (
        {"status": "pending_min_n", "n": 0}
        if not cost_pairs
        else {"status": "ok", "n": len(cost_pairs), "mse": mse(cost_pairs),
              "bias": signed_bias(cost_pairs)}
    )
    return {
        "risk_binary": _binary(RISK_BINARY),
        "benefit_binary": _binary(BENEFIT_BINARY),
        "benefit_continuous": continuous,
        "cost_continuous": cost_continuous,
        "observed": len(observed),
        "censored": len(rows) - len(observed),
        "total": len(rows),
    }


def build_error_rows(
    predictions: list[dict[str, Any]],
    labels: dict[str, Any],
    *,
    measured_benefit: float | None = None,
    measured_deltas: dict[str, float] | None = None,
    calibration_scope: str = "shadow",
    shadow_mode: int = 1,
) -> list[dict[str, Any]]:
    """Join captured predictions to outcome labels into prediction-error rows. Computes errors only
    where a real label exists; everything else is recorded ``censored`` (never guessed).

    ``shadow_mode``/``calibration_scope`` are stamped at build time (the caller decides eligibility from
    the terminal outcome). shadow_mode=0 + proceeded_edits_only = production calibration row. They are
    set here, never UPDATEd later — shadow_mode is in the prediction_errors hash canonical, so a post-hoc
    flip would break the chain."""
    measured_deltas = measured_deltas or {}
    rows: list[dict[str, Any]] = []
    for pred in predictions:
        tt, tn = pred["target_type"], pred["target_name"]
        pv = pred.get("predicted_value")
        row: dict[str, Any] = {
            "action_id": pred.get("action_id"),
            "target_type": tt,
            "target_name": tn,
            "calibration_scope": calibration_scope,
            "shadow_mode": shadow_mode,
            "outcome_label_status": "censored",
        }
        if tt in (RISK_BINARY, BENEFIT_BINARY):
            row["predicted_probability"] = pv
            actual = (
                _risk_actual(tn, labels)
                if tt == RISK_BINARY
                else (int(labels["benefit_realized"]) if isinstance(labels.get("benefit_realized"), bool) else None)
            )
            if actual is not None:
                row.update(
                    actual_outcome=actual,
                    brier_error=brier_score(pv, actual),
                    log_loss=log_loss_single(pv, actual),
                    residual=residual(pv, actual),
                    outcome_label_status="observed",
                )
        elif tt in (BENEFIT_CONTINUOUS, COST_CONTINUOUS):
            row["predicted_value"] = pv
            actual_value = _continuous_actual(tn, labels, measured_benefit, measured_deltas)
            if actual_value is not None:
                row.update(
                    actual_value=actual_value,
                    squared_error=squared_error(pv, actual_value),
                    residual=residual(pv, actual_value),
                    outcome_label_status="observed",
                )
        rows.append(row)
    return rows
