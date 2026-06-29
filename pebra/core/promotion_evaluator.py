"""promotion_evaluator (M5d / AD-18 §12.6) — pure, stdlib + core only.

Decides whether a candidate learned fact should be promoted, via **leave-one-out (LOO) counterfactual
replay**. The candidate's value is the observed harmful rate over its scope-matched rows; but the gate
scores each row out-of-sample — each row is predicted by the empirical rate computed *excluding that
row*. This matters because the in-sample mean is the Brier minimizer, so all-rows replay would make the
delta_brier gate self-fulfilling; LOO keeps the full sample while making each evaluation honest.

Gate (promote only if ALL hold):
  delta_brier    = brier_without − brier_with     >= min_delta_brier      (default 0.0)
  delta_log_loss = logloss_without − logloss_with  >= min_delta_log_loss  (default 0.0)
  false_proceed_rate does NOT increase   — HARD VETO, p_event.* targets only
  C4 high-criticality not weakened       — p_event.* targets only
  n_group >= min_calibration_samples

Convention (y=1=harmful) applies to p_event.* targets; for p_success, actual_outcome=1 is success, so
the false-proceed / C4 vetoes are skipped (applying them would invert the safety check).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from pebra.core.constants import MIN_CALIBRATION_SAMPLES
from pebra.core.learning_eval import DecisionOutcome, false_proceed_rate
from pebra.core.prediction_error import mean_brier, mean_log_loss, mse

_LOW = "p_event."  # event-risk target prefix; false-proceed/C4 vetoes apply only to these


def compute_empirical_value(rows: list[dict[str, Any]]) -> float:
    """The learned fact value = raw observed harmful rate over the scope-matched rows.

    No shrinkage in v1 (the N-gate + LOO replay are the noise defense; a smoothing prior has no
    calibrated strength yet — deferred to v1.5). Raises on empty input.
    """
    if not rows:
        raise ValueError("cannot compute empirical value over empty rows")
    return sum(int(r["actual_outcome"]) for r in rows) / len(rows)


def scope_matches_features(
    scope_kind: str, scope_value: str, scope_json: dict[str, Any], features: dict[str, Any]
) -> bool:
    """Pure features-dict scope matching — mirrors apply_snapshot._matches but over the stored
    ``features`` payload (not AssessmentInput). path_glob matches the captured file_path only."""
    if scope_kind == "global":
        return True
    sym = features.get("symbol") or {}
    st = features.get("structural") or {}
    domains = (features.get("domain") or {}).get("matched_domains") or []
    if scope_kind == "action_type":
        return sym.get("action_type") == scope_value
    if scope_kind == "path_glob":
        fp = sym.get("file_path")
        return bool(fp) and fnmatch.fnmatch(fp, scope_value)
    if scope_kind == "symbol":
        return sym.get("symbol_id") == scope_value
    if scope_kind == "public_api":
        return bool(sym.get("is_public_api"))
    if scope_kind == "public_api_domain":
        return bool(sym.get("is_public_api")) and scope_json.get("domain") in domains
    if scope_kind == "domain":
        return scope_value in domains
    if scope_kind == "domain_change_kind":
        return scope_json.get("domain") in domains and scope_json.get("change_kind") == sym.get("change_kind")
    if scope_kind == "high_symbol_fan_in":
        threshold = _float(scope_json.get("min_percentile", 0.90), 0.90)
        return bool(st.get("is_high_symbol_fan_in")) or _float(st.get("symbol_fan_in_percentile", 0.0)) >= threshold
    if scope_kind == "domain_high_symbol_fan_in":
        threshold = _float(scope_json.get("min_percentile", 0.90), 0.90)
        return scope_json.get("domain") in domains and _float(st.get("symbol_fan_in_percentile", 0.0)) >= threshold
    return False


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PromotionConfig:
    min_delta_brier: float = 0.0
    min_delta_log_loss: float = 0.0
    min_delta_mse: float = 0.0  # benefit_continuous gate (AD-29)
    min_calibration_samples: int = MIN_CALIBRATION_SAMPLES
    # Drift-freeze (snapshot_reconciler): pause promotion when the active snapshot has diverged from
    # the ledger by >= this. None disables the check (backward-compatible default).
    drift_freeze_threshold: float | None = None
    # false-proceed proxy fallback when a row carries no per-event disutility: a risk prediction below
    # this is treated as a proceed (flat, probability-only).
    decision_threshold: float = 0.5
    # disutility-aware false-proceed proxy (Item 5, prior_uncalibrated): a row is "concerned" when
    # p_event * event_disutility >= event_concern_budget. Severity-aware (a rare-but-catastrophic event
    # is concerning at a lower probability) and decoupled from the actual proceed-decision, so it stays
    # functional on the proceeded-only corpus (a faithful proceed-replay would saturate the veto). The
    # exact non-proceeded/canary replay is a Phase 5b DATA requirement, not a design deferral.
    event_concern_budget: float = 0.10


@dataclass(frozen=True)
class CandidateFact:
    target_name: str
    target_type: str
    scope_kind: str
    scope_value: str
    scope_json: dict[str, Any] = field(default_factory=dict)
    value: float = 0.0
    sample_size: int = 0
    specificity_rank: int = 0


@dataclass(frozen=True)
class PromotionGateResult:
    promoted: bool
    veto_reason: str | None
    delta_brier: float | None
    delta_log_loss: float | None
    brier_without: float | None
    brier_with: float | None
    log_loss_without: float | None
    log_loss_with: float | None
    false_proceed_rate_without: float | None
    false_proceed_rate_with: float | None
    c4_weakening_detected: bool
    n_group: int
    n_eval: int
    delta_mse: float | None = None  # benefit_continuous gate only (AD-29)


def _criticality(row: dict[str, Any]) -> str:
    return ((row.get("features") or {}).get("domain") or {}).get("criticality_stage", "")


def evaluate_promotion_gate(
    candidate: CandidateFact, all_event_rows: list[dict[str, Any]], config: PromotionConfig
) -> PromotionGateResult:
    """LOO counterfactual replay gate. ``all_event_rows`` is the full corpus for ``candidate.target_name``
    (caller-filtered by target). The candidate is scored on its matched scope only, out-of-sample via
    leave-one-out, so positive delta thresholds are not diluted by unrelated rows."""
    matched_idx = [
        i for i, r in enumerate(all_event_rows)
        if scope_matches_features(candidate.scope_kind, candidate.scope_value, candidate.scope_json,
                                  r.get("features") or {})
    ]
    n_group = len(matched_idx)
    n_eval = len(all_event_rows)
    if n_group < config.min_calibration_samples or n_group < 2:
        return PromotionGateResult(
            promoted=False, veto_reason="INSUFFICIENT_N", delta_brier=None, delta_log_loss=None,
            brier_without=None, brier_with=None, log_loss_without=None, log_loss_with=None,
            false_proceed_rate_without=None, false_proceed_rate_with=None,
            c4_weakening_detected=False, n_group=n_group, n_eval=n_eval,
        )

    s_total = sum(int(all_event_rows[i]["actual_outcome"]) for i in matched_idx)

    pairs_without: list[tuple[float, int]] = []
    pairs_with: list[tuple[float, int]] = []
    for i in matched_idx:
        r = all_event_rows[i]
        p0 = float(r["predicted_probability"])
        y = int(r["actual_outcome"])
        p1 = (s_total - y) / (n_group - 1)  # leave-one-out empirical rate (out-of-sample)
        pairs_without.append((p0, y))
        pairs_with.append((p1, y))

    brier_without = mean_brier(pairs_without)
    brier_with = mean_brier(pairs_with)
    log_loss_without = mean_log_loss(pairs_without)
    log_loss_with = mean_log_loss(pairs_with)
    delta_brier = brier_without - brier_with
    delta_log_loss = log_loss_without - log_loss_with

    is_event = candidate.target_name.startswith(_LOW)
    fpr_without: float | None = None
    fpr_with: float | None = None
    c4_weak = False
    if is_event:
        oc_without, oc_with, oc_without_c4, oc_with_c4 = [], [], [], []
        for i in matched_idx:
            r = all_event_rows[i]
            y = int(r["actual_outcome"])
            stage = _criticality(r)
            p0 = float(r["predicted_probability"])
            p1 = (s_total - y) / (n_group - 1)
            # disutility-aware proxy when the row carries its (post-floor) event disutility; else the
            # flat probability fallback. "concerned" = p * disutility >= budget; proceeded = not concerned.
            dis = r.get("event_disutility")
            if dis is not None:
                dis = float(dis)
                proceeded0 = (p0 * dis) < config.event_concern_budget
                proceeded1 = (p1 * dis) < config.event_concern_budget
            else:
                proceeded0 = p0 < config.decision_threshold
                proceeded1 = p1 < config.decision_threshold
            ow = DecisionOutcome(proceeded=proceeded0, harmful=bool(y), criticality_stage=stage)
            ww = DecisionOutcome(proceeded=proceeded1, harmful=bool(y), criticality_stage=stage)
            oc_without.append(ow)
            oc_with.append(ww)
            if stage == "C4":
                oc_without_c4.append(ow)
                oc_with_c4.append(ww)
        fpr_without = false_proceed_rate(oc_without)
        fpr_with = false_proceed_rate(oc_with)
        c4_w = false_proceed_rate(oc_with_c4)
        c4_wo = false_proceed_rate(oc_without_c4)
        c4_weak = c4_w is not None and (c4_wo is None or c4_w > c4_wo)

    veto = None
    if delta_brier < config.min_delta_brier:
        veto = "DELTA_BRIER_NEGATIVE"
    elif delta_log_loss < config.min_delta_log_loss:
        veto = "DELTA_LOG_LOSS_NEGATIVE"
    elif is_event and _false_proceed_increased(fpr_without, fpr_with):
        veto = "FALSE_PROCEED_RATE_INCREASE"
    elif is_event and c4_weak:
        veto = "C4_WEAKENING_DETECTED"

    return PromotionGateResult(
        promoted=veto is None, veto_reason=veto, delta_brier=delta_brier,
        delta_log_loss=delta_log_loss, brier_without=brier_without, brier_with=brier_with,
        log_loss_without=log_loss_without, log_loss_with=log_loss_with,
        false_proceed_rate_without=fpr_without, false_proceed_rate_with=fpr_with,
        c4_weakening_detected=c4_weak, n_group=n_group, n_eval=n_eval,
    )


def compute_empirical_continuous_value(rows: list[dict[str, Any]]) -> float:
    """The learned continuous fact value = mean observed actual_value over the scope-matched rows."""
    if not rows:
        raise ValueError("cannot compute empirical continuous value over empty rows")
    return sum(float(r["actual_value"]) for r in rows) / len(rows)


def evaluate_benefit_continuous_gate(
    candidate: CandidateFact, all_rows: list[dict[str, Any]], config: PromotionConfig
) -> PromotionGateResult:
    """AD-29 benefit-continuous promotion gate. LOO-MSE replay: does the learned empirical mean predict
    the actual_value better (out-of-sample) than the model's predicted_value? No false-proceed / C4 veto
    — benefit is not harm. Matched-scope only (positive thresholds not diluted)."""
    matched_idx = [
        i for i, r in enumerate(all_rows)
        if scope_matches_features(candidate.scope_kind, candidate.scope_value, candidate.scope_json,
                                  r.get("features") or {})
    ]
    n_group = len(matched_idx)
    n_eval = len(all_rows)
    none_result = dict(
        delta_brier=None, delta_log_loss=None, brier_without=None, brier_with=None,
        log_loss_without=None, log_loss_with=None, false_proceed_rate_without=None,
        false_proceed_rate_with=None, c4_weakening_detected=False,
    )
    if n_group < config.min_calibration_samples or n_group < 2:
        return PromotionGateResult(
            promoted=False, veto_reason="INSUFFICIENT_N", n_group=n_group, n_eval=n_eval,
            delta_mse=None, **none_result,
        )

    s_total = sum(float(all_rows[i]["actual_value"]) for i in matched_idx)
    pairs_without: list[tuple[float, float]] = []
    pairs_with: list[tuple[float, float]] = []
    for i in matched_idx:
        r = all_rows[i]
        p0 = float(r["predicted_value"])
        a = float(r["actual_value"])
        p1 = (s_total - a) / (n_group - 1)  # leave-one-out empirical mean (out-of-sample)
        pairs_without.append((p0, a))
        pairs_with.append((p1, a))
    delta_mse = mse(pairs_without) - mse(pairs_with)
    veto = "DELTA_MSE_NEGATIVE" if delta_mse < config.min_delta_mse else None
    return PromotionGateResult(
        promoted=veto is None, veto_reason=veto, n_group=n_group, n_eval=n_eval,
        delta_mse=delta_mse, **none_result,
    )


def _false_proceed_increased(without: float | None, with_: float | None) -> bool:
    """The fact must not raise the rate at which harmful edits proceed. A rate appearing where there
    was none (None -> a value) is treated as an increase (new harmful-proceed risk)."""
    if with_ is None:
        return False
    if without is None:
        return True
    return with_ > without
