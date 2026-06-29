"""apply_snapshot (M5b) — pure learned-override reapplication.

Takes a (read-port-decoded) SnapshotBundle of learned facts and the raw AssessmentInput, and returns
an adjusted copy where matching facts have OVERRIDDEN predicted risk probabilities (p_success,
p_event.<event>) and benefit inputs PRE-scoring. Pure stdlib + core only — no DB, no I/O, no
learning writes.

v1 semantics (ratified, deliberately simple): most-specific scope wins (k=1), REPLACEMENT — i.e. a
"learned override", NOT a blended calibrated prior. The references (Calibrate-Then-Act, MICE) treat a
calibrated value as a monotone remap of / blend with the model's own belief; logit-space
confidence-weighted blending is the v2 roadmap. v1 overrides outright and is honestly named so.

Safety rails:
- ``risk_binary`` targets adjust risk probabilities; benefit targets adjust immediate benefit,
  maintainability deltas, or the final measured-benefit override.
- defense-in-depth: facts requiring human ratification, or with no calibration sample, are not applied
  (the read-port is the primary gate: status='active', ratified, min sample size).
- stale/weak facts whose churn-decayed reliability falls below the auto-apply threshold are skipped
  on both hard-replace and log-pool paths.
- adjusted probabilities clamped to [0.01, 0.99] (logit safety; intentionally stricter than the
  capture clamp of [0,1] in prediction_capture).
- the original input is never mutated; adjusted event dicts are deep-copied.
- snapshot is None / no facts / no match -> the input is returned unchanged (byte-equivalent).
"""

from __future__ import annotations

import dataclasses
import fnmatch
import math
from dataclasses import dataclass, field
from typing import Any

from pebra.core import risk_fact_decay
from pebra.core.models import AssessmentInput
from pebra.core.prediction_capture import BENEFIT_BINARY, BENEFIT_CONTINUOUS, RISK_BINARY

_CLAMP_LO = 0.01
_CLAMP_HI = 0.99


@dataclass(frozen=True)
class SnapshotFact:
    """One learned fact decoded from learned_risk_facts by the (M5c) read-port. ``value`` is the
    learned override for ``target_name`` within ``scope``. ``fact_json`` carries composite scope
    predicate data (e.g. domain+change_kind). The read-port supplies ``fact_id`` as ``lrf_{id}`` and
    is the primary applicability gate; the fields here also enable a deterministic tiebreak."""

    fact_id: str
    target_type: str
    target_name: str
    scope_kind: str
    scope_value: str
    specificity_rank: int
    value: float
    sample_size: int = 0
    calibration_method: str = ""
    created_at: str = ""
    weight: float = 1.0
    requires_human_ratification: bool = False
    scope_json: dict[str, Any] = field(default_factory=dict)
    # Read-side enriched reliability inputs. Hard-replace uses them only for auto-apply eligibility;
    # log-pool also uses them as the contributor weight.
    # ``calibration_quality`` scales the base reliability weight; ``scope_change_count`` is scope churn
    # since the fact was learned (drives AD-17 decay).
    calibration_quality: float = 1.0
    scope_change_count: int = 0


@dataclass(frozen=True)
class SnapshotBundle:
    snapshot_id: str
    facts: tuple[SnapshotFact, ...] = ()


@dataclass(frozen=True)
class PoolConfig:
    """How matching facts are combined (AD-20). ``hard_replace`` (default) is the v1 winner-take-all
    path. ``log_pool`` combines the top-k matching facts in logit space, weighted by churn-decayed
    reliability, anchored to the model's prior so the pooled value cannot move more than
    ``max_logit_shift`` logits away from the original belief."""

    mode: str = "hard_replace"  # "hard_replace" | "log_pool"
    top_k: int = 1
    max_logit_shift: float = 2.0


def _validate_pool_config(cfg: PoolConfig | None) -> PoolConfig | None:
    if cfg is None:
        return None
    if cfg.mode not in {"hard_replace", "log_pool"}:
        raise ValueError(f"unsupported pool mode: {cfg.mode!r}")
    if cfg.top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {cfg.top_k}")
    if cfg.max_logit_shift < 0.0 or not math.isfinite(cfg.max_logit_shift):
        raise ValueError(f"max_logit_shift must be finite and >= 0, got {cfg.max_logit_shift}")
    return cfg


def _clamp(value: float) -> float:
    return max(_CLAMP_LO, min(_CLAMP_HI, value))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _applicable(fact: SnapshotFact) -> bool:
    """Defense-in-depth gate (read-port is primary): a fact may override a live risk probability only
    if it is risk-binary, ratified, carries a calibration sample AND method, and its value is a finite
    number. A non-finite or method-less value is treated as malformed and skipped — never clamped into
    a strong override."""
    return (
        fact.target_type in {RISK_BINARY, BENEFIT_BINARY, BENEFIT_CONTINUOUS}
        and not fact.requires_human_ratification
        and fact.sample_size > 0
        and bool(fact.calibration_method)
        and math.isfinite(fact.value)
    )


def _matches(fact: SnapshotFact, inp: AssessmentInput, features: dict[str, Any] | None) -> bool:
    sk = fact.scope_kind
    # input-derived scopes (no structural features required)
    if sk == "global":
        return True
    if sk == "action_type":
        return inp.action.action_type == fact.scope_value
    if sk == "path_glob":
        # match ALL files the action touches (not just the representative one) + the structural file
        paths = list(inp.action.expected_files)
        if features:
            file_path = (features.get("symbol") or {}).get("file_path")
            if file_path:
                paths.append(file_path)
        return any(fnmatch.fnmatch(p, fact.scope_value) for p in paths)
    # the remaining scopes need the structural feature payload
    if features is None:
        return False
    sym = features.get("symbol", {})
    st = features.get("structural", {}) or {}
    domains = (features.get("domain", {}) or {}).get("matched_domains") or []
    if sk == "symbol":
        return sym.get("symbol_id") == fact.scope_value
    if sk == "public_api":
        return bool(sym.get("is_public_api"))
    if sk == "public_api_domain":
        return bool(sym.get("is_public_api")) and fact.scope_json.get("domain") in domains
    if sk == "domain":
        return fact.scope_value in domains
    if sk == "domain_change_kind":
        return (
            fact.scope_json.get("domain") in domains
            and fact.scope_json.get("change_kind") == sym.get("change_kind")
        )
    if sk == "high_symbol_fan_in":
        # per-symbol fan-in lives in the STRUCTURAL block of the v2 feature payload (alongside the
        # container/file fan-in), NOT the symbol block — read it there.
        threshold = _float(fact.scope_json.get("min_percentile", 0.90), 0.90)
        return bool(st.get("is_high_symbol_fan_in")) or (
            _float(st.get("symbol_fan_in_percentile", 0.0)) >= threshold
        )
    if sk == "domain_high_symbol_fan_in":
        threshold = _float(fact.scope_json.get("min_percentile", 0.90), 0.90)
        return (
            fact.scope_json.get("domain") in domains
            and _float(st.get("symbol_fan_in_percentile", 0.0)) >= threshold
        )
    return False


def _winner(
    facts: tuple[SnapshotFact, ...], target_name: str, inp: AssessmentInput,
    features: dict[str, Any] | None,
) -> SnapshotFact | None:
    """Most-specific-wins (k=1) with a deterministic tiebreak on equal specificity:
    specificity_rank -> sample_size -> created_at (newest) -> fact_id."""
    candidates = _candidates(facts, target_name, inp, features)
    if not candidates:
        return None
    return candidates[0]


def _provenance(target: str, prior: float, new: float, fact: SnapshotFact) -> dict[str, Any]:
    return {
        "target": target,
        "winning_fact_id": fact.fact_id,
        "scope_kind": fact.scope_kind,
        "scope_rank": fact.specificity_rank,
        "prior_predicted_p": prior,
        "new_value": new,
        "sample_size": fact.sample_size,
        "calibration_method": fact.calibration_method,
    }


def _effective_fact_weight(fact: SnapshotFact) -> float | None:
    try:
        weight = float(fact.weight)
        quality = float(fact.calibration_quality)
        count = int(fact.scope_change_count)
    except (TypeError, ValueError):
        return None
    if weight < 0.0 or quality < 0.0 or count < 0:
        return None
    if not math.isfinite(weight) or not math.isfinite(quality):
        return None
    try:
        decayed = risk_fact_decay.effective_weight(weight * quality, count)
    except (TypeError, ValueError, OverflowError):
        return None
    return decayed if math.isfinite(decayed) else None


def _auto_applies(fact: SnapshotFact) -> bool:
    weight = _effective_fact_weight(fact)
    return weight is not None and risk_fact_decay.should_auto_apply(weight)


def _logit(p: float) -> float:
    p = _clamp(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    # numerically stable: avoid exp() overflow on large |x| (e.g. a far-off pooled logit).
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _candidates(
    facts: tuple[SnapshotFact, ...], target_name: str, inp: AssessmentInput,
    features: dict[str, Any] | None,
) -> list[SnapshotFact]:
    """Applicable + matching facts for a target, most-specific first (same order key as ``_winner``)."""
    cands = [
        f for f in facts
        if (
            f.target_name == target_name
            and _applicable(f)
            and _auto_applies(f)
            and _matches(f, inp, features)
        )
    ]
    cands.sort(
        key=lambda f: (f.specificity_rank, f.sample_size, f.created_at, f.fact_id), reverse=True
    )
    return cands


def _pool_value(
    prior: float, candidates: list[SnapshotFact], cfg: PoolConfig
) -> tuple[float | None, list[tuple[SnapshotFact, float]]]:
    """Logit-space reliability-weighted pool of the top-k candidates, anchored to ``prior``.

    Each candidate's weight is its churn-decayed reliability (AD-17); candidates that decay below the
    auto-apply threshold are dropped. The pooled logit cannot move more than ``cfg.max_logit_shift``
    from ``logit(prior)``. Returns ``(None, [])`` when no candidate survives.
    """
    weighted: list[tuple[SnapshotFact, float]] = []
    for f in candidates[: cfg.top_k]:
        w = _effective_fact_weight(f)
        if w is None or not risk_fact_decay.should_auto_apply(w):
            continue
        weighted.append((f, w))
    if not weighted:
        return None, []
    total = math.fsum(w for _, w in weighted)
    pooled_logit = math.fsum(w * _logit(f.value) for f, w in weighted) / total
    anchor = _logit(prior)
    lo, hi = anchor - cfg.max_logit_shift, anchor + cfg.max_logit_shift
    pooled_logit = max(lo, min(hi, pooled_logit))
    return _clamp(_sigmoid(pooled_logit)), weighted


def _pool_provenance(
    target: str, prior: float, new: float, contributors: list[tuple[SnapshotFact, float]]
) -> dict[str, Any]:
    return {
        "target": target,
        "mode": "log_pool",
        "prior_predicted_p": prior,
        "new_value": new,
        "pooled_facts": [
            {
                "fact_id": f.fact_id, "scope_kind": f.scope_kind, "scope_rank": f.specificity_rank,
                "value": f.value, "pool_weight": w, "sample_size": f.sample_size,
                "calibration_method": f.calibration_method,
            }
            for f, w in contributors
        ],
    }


def _resolve_target(
    facts: tuple[SnapshotFact, ...], target_name: str, prior: float, inp: AssessmentInput,
    features: dict[str, Any] | None, cfg: PoolConfig | None, *, probability: bool = True,
) -> tuple[float | None, dict[str, Any] | None]:
    """Resolve one target to ``(new_value, provenance)`` or ``(None, None)`` when nothing applies.

    ``cfg is None`` or ``mode == "hard_replace"`` uses the v1 winner-take-all path after
    auto-apply eligibility filtering; ``mode == "log_pool"`` uses reliability-weighted logit pooling.
    """
    if not probability or cfg is None or cfg.mode == "hard_replace":
        winner = _winner(facts, target_name, inp, features)
        if winner is None:
            return None, None
        new = _clamp(winner.value) if probability else winner.value
        return new, _provenance(target_name, prior, new, winner)
    new, contributors = _pool_value(prior, _candidates(facts, target_name, inp, features), cfg)
    if new is None:
        return None, None
    return new, _pool_provenance(target_name, prior, new, contributors)


def apply_snapshot(
    inp: AssessmentInput,
    snapshot: SnapshotBundle | None = None,
    pool_config: PoolConfig | None = None,
) -> AssessmentInput:
    """Return ``inp`` unchanged when no active snapshot/fact applies; otherwise an adjusted copy with
    overridden risk probabilities and ``applied_snapshot_provenance`` set.

    ``pool_config`` defaults to ``None`` == v1 hard-replace; ``PoolConfig(mode=
    "log_pool", ...)`` opts into top-k reliability-weighted logit pooling (AD-20)."""
    pool_config = _validate_pool_config(pool_config)
    if snapshot is None or not snapshot.facts:
        return inp

    features = inp.structural_features
    applied: list[dict[str, Any]] = []

    new_p_success = inp.p_success
    val, prov = _resolve_target(
        snapshot.facts, "p_success", inp.p_success, inp, features, pool_config
    )
    if val is not None:
        new_p_success = val
        applied.append(prov)  # type: ignore[arg-type]

    new_events: list[dict[str, Any]] | None = None
    for idx, event in enumerate(inp.events):
        prior = event["p_event"]
        val, prov = _resolve_target(
            snapshot.facts, f"p_event.{event['event']}", prior, inp, features, pool_config
        )
        if val is None:
            continue
        if new_events is None:
            # shallow copy is sufficient: v1 event dicts hold only scalars (event/p_event/
            # elicited_disutility). Revisit if the event schema ever gains nested mutables.
            new_events = [dict(e) for e in inp.events]  # never mutate the original
        new_events[idx]["p_event"] = val
        applied.append(prov)  # type: ignore[arg-type]

    new_immediate_benefit = inp.immediate_benefit
    val, prov = _resolve_target(
        snapshot.facts, "immediate_benefit_realized", inp.immediate_benefit,
        inp, features, pool_config,
    )
    if val is not None:
        new_immediate_benefit = val
        applied.append(prov)  # type: ignore[arg-type]

    new_benefit_delta_evidence = inp.benefit_delta_evidence
    delta_targets = sorted({
        fact.target_name for fact in snapshot.facts
        if fact.target_name.startswith("maintainability_delta.")
    })
    for target_name in delta_targets:
        if not target_name.startswith("maintainability_delta."):
            continue
        metric = target_name.split(".", 1)[1]
        prior = inp.benefit_delta_evidence.deltas.get(metric, 0.0)
        val, prov = _resolve_target(
            snapshot.facts, target_name, prior, inp, features, None, probability=False
        )
        if val is None:
            continue
        deltas = dict(new_benefit_delta_evidence.deltas)
        deltas[metric] = val
        new_benefit_delta_evidence = dataclasses.replace(
            new_benefit_delta_evidence,
            deltas=deltas,
            source_type="learned_override",
        )
        applied.append(prov)  # type: ignore[arg-type]

    new_benefit_override = inp.benefit_override
    val, prov = _resolve_target(
        snapshot.facts, "measured_benefit", inp.benefit_override or inp.immediate_benefit,
        inp, features, None, probability=False,
    )
    if val is not None:
        new_benefit_override = val
        applied.append(prov)  # type: ignore[arg-type]

    if not applied:
        return inp  # no matching facts -> byte-equivalent

    return dataclasses.replace(
        inp,
        p_success=new_p_success,
        events=new_events if new_events is not None else inp.events,
        immediate_benefit=new_immediate_benefit,
        benefit_delta_evidence=new_benefit_delta_evidence,
        benefit_override=new_benefit_override,
        applied_snapshot_provenance={"snapshot_id": snapshot.snapshot_id, "applied_facts": applied},
    )
