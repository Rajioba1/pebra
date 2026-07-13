"""promotion_controller (M5d) — orchestrates shadow→active learned-fact promotion.

Pure port DI: loads production calibration rows via StorePort, derives scope candidates from each row's
features payload, runs the leave-one-out promotion gate (core/promotion_evaluator) per candidate, and
writes promoted facts via LearningPort. Imports only ports + core (enforced by app-no-adapters). This
is on the WRITE side — assess never imports it (assess-no-learning).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from pebra.core import promotion_evaluator as pe
from pebra.core import snapshot_reconciler
from pebra.core.prediction_capture import (
    BENEFIT_BINARY,
    BENEFIT_CONTINUOUS,
    COST_CONTINUOUS,
    RISK_BINARY,
)
from pebra.ports.learning_port import LearningPort
from pebra.ports.store_port import StorePort

# scope_kind -> specificity_rank (higher = more specific; mirrors apply_snapshot tie-break ordering).
_SCOPE_SPECIFICITY: dict[str, int] = {
    "global": 0, "action_type": 2, "public_api": 3, "domain": 4,
    "domain_change_kind": 5, "high_symbol_fan_in": 6,
    "domain_high_symbol_fan_in": 7, "public_api_domain": 8, "symbol": 9,
}


@dataclass
class PromotionResult:
    repo_id: str
    promoted: bool
    snapshot_id: str | None
    fact_ids: list[str]
    facts_considered: int
    facts_promoted: int
    facts_vetoed: int
    veto_reasons: list[str] = field(default_factory=list)
    drift_score: float | None = None
    frozen_due_to_drift: bool = False


def _sym(row: dict[str, Any]) -> dict[str, Any]:
    return (row.get("features") or {}).get("symbol") or {}


def _structural(row: dict[str, Any]) -> dict[str, Any]:
    return (row.get("features") or {}).get("structural") or {}


def _domains(row: dict[str, Any]) -> list[str]:
    return ((row.get("features") or {}).get("domain") or {}).get("matched_domains") or []


def _cand(target_name: str, scope_kind: str, scope_value: str = "",
          scope_json: dict[str, Any] | None = None,
          target_type: str = RISK_BINARY) -> pe.CandidateFact:
    return pe.CandidateFact(
        target_name=target_name, target_type=target_type, scope_kind=scope_kind,
        scope_value=scope_value, scope_json=scope_json or {},
        specificity_rank=_SCOPE_SPECIFICITY[scope_kind],
    )


def _derive_scope_candidates(
    rows: list[dict[str, Any]], target_name: str, target_type: str = RISK_BINARY
) -> list[pe.CandidateFact]:
    """Derive the candidate scopes that the features payload supports. ``path_glob`` is deliberately
    not auto-derived here; extracting useful globs from arbitrary repo layout is policy-laden."""
    def c(scope_kind, scope_value="", scope_json=None):
        return _cand(target_name, scope_kind, scope_value, scope_json, target_type)

    cands: list[pe.CandidateFact] = [c("global")]
    action_types = {at for r in rows if (at := _sym(r).get("action_type"))}
    cands += [c("action_type", at) for at in sorted(action_types)]
    if any(_sym(r).get("is_public_api") for r in rows):
        cands.append(c("public_api"))
    if any(_structural(r).get("is_high_symbol_fan_in") for r in rows):
        cands.append(c("high_symbol_fan_in", scope_json={"min_percentile": 0.90}))
    symbol_ids = {sid for r in rows if (sid := _sym(r).get("symbol_id"))}
    cands += [c("symbol", sid) for sid in sorted(symbol_ids)]
    all_domains = sorted({d for r in rows for d in _domains(r)})
    cands += [c("domain", d) for d in all_domains]
    cands += [
        c("public_api_domain", scope_json={"domain": d})
        for d in all_domains
        if any(_sym(r).get("is_public_api") and d in _domains(r) for r in rows)
    ]
    domain_change_kinds = sorted({
        (d, ck) for r in rows if (ck := _sym(r).get("change_kind")) for d in _domains(r)
    })
    cands += [
        c("domain_change_kind", scope_json={"domain": d, "change_kind": ck})
        for d, ck in domain_change_kinds
    ]
    cands += [
        c("domain_high_symbol_fan_in", scope_json={"domain": d, "min_percentile": 0.90})
        for d in all_domains
    ]
    return cands


def _extract_provider_provenance(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Most common provider_version / index_version across the rows (provenance preserved in fact_json
    even though v1 does not group by version)."""
    provs = Counter()
    idxs = Counter()
    for r in rows:
        prov = (r.get("features") or {}).get("provenance") or {}
        if prov.get("provider_version"):
            provs[prov["provider_version"]] += 1
        if prov.get("index_version"):
            idxs[prov["index_version"]] += 1
    provider = provs.most_common(1)[0][0] if provs else None
    index = idxs.most_common(1)[0][0] if idxs else None
    return provider, index


def _collect_facts(
    all_rows: list[dict[str, Any]], *, config: pe.PromotionConfig, gate_fn, value_fn,
    calibration_method: str, variance_fn=None, variance_method: str | None = None,
    aleatoric_variance_fn=None,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Group rows by target, derive scope candidates, run the gate, and build promotable fact dicts.
    Shared by risk and benefit promotion — only the gate_fn (replay metric) + value_fn (fact value) +
    calibration_method differ. Returns (fact_dicts, veto_reasons, considered)."""
    by_target: dict[str, list[dict[str, Any]]] = {}
    for r in all_rows:
        refinement = (r.get("features") or {}).get("graph_refinement") or {}
        if refinement.get("status") == "available":
            # Candidate-conditioned updates cannot become global learned priors. Keep them in the
            # calibration ledger, but skip promotion until scopes can require the same graph fact.
            continue
        by_target.setdefault(r["target_name"], []).append(r)

    fact_dicts: list[dict[str, Any]] = []
    veto_reasons: list[str] = []
    considered = 0
    for target_name, target_rows in by_target.items():
        provider_v, index_v = _extract_provider_provenance(target_rows)
        target_type = target_rows[0]["target_type"]
        for candidate in _derive_scope_candidates(target_rows, target_name, target_type):
            considered += 1
            gate = gate_fn(candidate, target_rows, config)
            if not gate.promoted:
                if gate.veto_reason:
                    veto_reasons.append(gate.veto_reason)
                continue
            group_rows = [
                r for r in target_rows
                if pe.scope_matches_features(candidate.scope_kind, candidate.scope_value,
                                             candidate.scope_json, r.get("features") or {})
            ]
            fact_json = {
                "value": value_fn(group_rows),
                "weight": 1.0,
                "sample_size": len(group_rows),
                "calibration_method": calibration_method,
                "calibration_quality": 1.0,
                "scope_change_count": 0,
                "provider_version": provider_v,
                "index_version": index_v,
            }
            if variance_fn is not None:
                fact_json["variance"] = variance_fn(group_rows)
                fact_json["variance_method"] = variance_method
            if aleatoric_variance_fn is not None:
                fact_json["aleatoric_variance"] = aleatoric_variance_fn(group_rows)
            fact_dicts.append({
                "target_type": target_type,
                "target_name": candidate.target_name,
                "scope_kind": candidate.scope_kind,
                "scope_value": candidate.scope_value,
                "specificity_rank": candidate.specificity_rank,
                "scope_json": candidate.scope_json,
                "fact_json": fact_json,
                "fact_type": "learned_override",
                "status": "active",
                "requires_human_ratification": False,
            })
    return fact_dicts, veto_reasons, considered


def _result(repo_id, fact_dicts, veto_reasons, considered, *, learning_port,
            promotion_reason, drift_score: float | None = None,
            metrics_extra: dict[str, Any] | None = None,
            trigger_key: str | None = None) -> PromotionResult:
    if not fact_dicts:
        return PromotionResult(
            repo_id=repo_id, promoted=False, snapshot_id=None, fact_ids=[],
            facts_considered=considered, facts_promoted=0, facts_vetoed=len(veto_reasons),
            veto_reasons=veto_reasons or ["NO_CALIBRATION_ROWS"], drift_score=drift_score,
        )
    snapshot_metrics = {
        "promotion_reason": promotion_reason,
        "repo_id": repo_id,
        "facts_promoted": len(fact_dicts),
        "target_names": sorted({f["target_name"] for f in fact_dicts}),
        "hash_version": 2,
        "facts_fingerprint": hashlib.sha256(
            json.dumps(fact_dicts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    if trigger_key is not None:
        snapshot_metrics["trigger_key"] = trigger_key
    if drift_score is not None:
        snapshot_metrics["drift_score"] = drift_score
    if metrics_extra:
        snapshot_metrics.update(metrics_extra)
    snapshot_id, fact_ids = learning_port.write_promotion(repo_id, snapshot_metrics, fact_dicts)
    return PromotionResult(
        repo_id=repo_id, promoted=True, snapshot_id=snapshot_id, fact_ids=fact_ids,
        facts_considered=considered, facts_promoted=len(fact_dicts),
        facts_vetoed=len(veto_reasons), veto_reasons=veto_reasons, drift_score=drift_score,
    )


def run_promotion(
    repo_id: str,
    *,
    store: StorePort,
    learning_port: LearningPort,
    config: pe.PromotionConfig | None = None,
    trigger_key: str | None = None,
) -> PromotionResult:
    """Risk-binary promotion (AD-18). Decoupled from benefit — risk never waits on benefit. When a
    drift-freeze threshold is configured, promotion PAUSES if the active snapshot has diverged from the
    current ledger (snapshot_reconciler) — divergence is reviewed before more facts pile on."""
    config = config or pe.PromotionConfig()
    all_rows = store.load_production_calibration_rows(repo_id, RISK_BINARY)
    fact_dicts, veto_reasons, considered = _collect_facts(
        all_rows, config=config, gate_fn=pe.evaluate_promotion_gate,
        value_fn=pe.compute_empirical_value, calibration_method="observed_rate_v1",
        variance_fn=pe.beta_parameter_variance,
        variance_method="beta_1_1_parameter_variance",
        aleatoric_variance_fn=pe.binary_aleatoric_variance,
    )
    drift_score: float | None = None
    if config.drift_freeze_threshold is not None and fact_dicts:
        drift_score = snapshot_reconciler.compute_drift(
            store.read_active_snapshot_rows(repo_id), all_rows
        )
        if snapshot_reconciler.should_freeze(drift_score, config.drift_freeze_threshold):
            return PromotionResult(
                repo_id=repo_id, promoted=False, snapshot_id=None, fact_ids=[],
                facts_considered=considered, facts_promoted=0, facts_vetoed=0,
                veto_reasons=["DRIFT_FREEZE"], drift_score=drift_score, frozen_due_to_drift=True,
            )
    return _result(repo_id, fact_dicts, veto_reasons, considered,
                   learning_port=learning_port, promotion_reason="M5d_auto_promotion",
                   drift_score=drift_score,
                   metrics_extra={"event_concern_budget": config.event_concern_budget},
                   trigger_key=trigger_key)


def run_benefit_promotion(
    repo_id: str,
    *,
    store: StorePort,
    learning_port: LearningPort,
    config: pe.PromotionConfig | None = None,
    trigger_key: str | None = None,
) -> PromotionResult:
    """Benefit promotion (AD-29), DECOUPLED from risk: benefit_binary uses the Brier/log-loss gate
    (the false-proceed veto is naturally skipped — its target isn't p_event.*); benefit_continuous uses
    the LOO-MSE gate. Writes one benefit snapshot; the assess read path applies active benefit facts
    through ``apply_snapshot`` before scoring."""
    config = config or pe.PromotionConfig()
    fb, vb, cb = _collect_facts(
        store.load_production_calibration_rows(repo_id, BENEFIT_BINARY),
        config=config, gate_fn=pe.evaluate_promotion_gate,
        value_fn=pe.compute_empirical_value, calibration_method="observed_rate_v1",
        variance_fn=pe.beta_parameter_variance,
        variance_method="beta_1_1_parameter_variance",
        aleatoric_variance_fn=pe.binary_aleatoric_variance,
    )
    fc, vc, cc = _collect_facts(
        store.load_production_calibration_rows(repo_id, BENEFIT_CONTINUOUS),
        config=config, gate_fn=pe.evaluate_benefit_continuous_gate,
        value_fn=pe.compute_empirical_continuous_value, calibration_method="observed_mean_v1",
        variance_fn=pe.continuous_mean_variance,
        variance_method="sample_mean_variance",
        aleatoric_variance_fn=pe.continuous_aleatoric_variance,
    )
    return _result(repo_id, fb + fc, vb + vc, cb + cc,
                   learning_port=learning_port, promotion_reason="M5d_benefit_promotion",
                   trigger_key=trigger_key)


def run_review_cost_promotion(
    repo_id: str,
    *,
    store: StorePort,
    learning_port: LearningPort,
    config: pe.PromotionConfig | None = None,
    trigger_key: str | None = None,
) -> PromotionResult:
    """Promote observed review effort independently from risk and benefit."""
    config = config or pe.PromotionConfig()
    facts, vetoes, considered = _collect_facts(
        store.load_production_calibration_rows(repo_id, COST_CONTINUOUS),
        config=config,
        gate_fn=pe.evaluate_benefit_continuous_gate,
        value_fn=pe.compute_empirical_continuous_value,
        calibration_method="observed_mean_v1",
        variance_fn=pe.continuous_mean_variance,
        variance_method="sample_mean_variance",
        aleatoric_variance_fn=pe.continuous_aleatoric_variance,
    )
    return _result(
        repo_id,
        facts,
        vetoes,
        considered,
        learning_port=learning_port,
        promotion_reason="M5d_review_cost_promotion",
        trigger_key=trigger_key,
    )
