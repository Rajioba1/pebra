"""promotion_controller (M5d) — orchestrates shadow→active learned-fact promotion.

Pure port DI: loads production calibration rows via StorePort, derives scope candidates from each row's
features payload, runs the leave-one-out promotion gate (core/promotion_evaluator) per candidate, and
writes promoted facts via LearningPort. Imports only ports + core (enforced by app-no-adapters). This
is on the WRITE side — assess never imports it (assess-no-learning).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pebra.core import promotion_evaluator as pe
from pebra.core.prediction_capture import RISK_BINARY
from pebra.ports.learning_port import LearningPort
from pebra.ports.store_port import StorePort

# scope_kind -> specificity_rank (higher = more specific; mirrors apply_snapshot tie-break ordering).
_SCOPE_SPECIFICITY: dict[str, int] = {
    "global": 0, "action_type": 2, "public_api": 3, "domain": 4,
    "high_symbol_fan_in": 6, "domain_high_symbol_fan_in": 7, "symbol": 9,
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


def _sym(row: dict[str, Any]) -> dict[str, Any]:
    return (row.get("features") or {}).get("symbol") or {}


def _structural(row: dict[str, Any]) -> dict[str, Any]:
    return (row.get("features") or {}).get("structural") or {}


def _domains(row: dict[str, Any]) -> list[str]:
    return ((row.get("features") or {}).get("domain") or {}).get("matched_domains") or []


def _cand(target_name: str, scope_kind: str, scope_value: str = "",
          scope_json: dict[str, Any] | None = None) -> pe.CandidateFact:
    return pe.CandidateFact(
        target_name=target_name, target_type=RISK_BINARY, scope_kind=scope_kind,
        scope_value=scope_value, scope_json=scope_json or {},
        specificity_rank=_SCOPE_SPECIFICITY[scope_kind],
    )


def _derive_scope_candidates(rows: list[dict[str, Any]], target_name: str) -> list[pe.CandidateFact]:
    """Derive the candidate scopes that the features payload supports (v1 set). path_glob and the
    composite scopes (public_api_domain, domain_change_kind) are deferred — not auto-derived here."""
    cands: list[pe.CandidateFact] = [_cand(target_name, "global")]
    action_types = {at for r in rows if (at := _sym(r).get("action_type"))}
    cands += [_cand(target_name, "action_type", at) for at in sorted(action_types)]
    if any(_sym(r).get("is_public_api") for r in rows):
        cands.append(_cand(target_name, "public_api"))
    if any(_structural(r).get("is_high_symbol_fan_in") for r in rows):
        cands.append(_cand(target_name, "high_symbol_fan_in", scope_json={"min_percentile": 0.90}))
    symbol_ids = {sid for r in rows if (sid := _sym(r).get("symbol_id"))}
    cands += [_cand(target_name, "symbol", sid) for sid in sorted(symbol_ids)]
    all_domains = sorted({d for r in rows for d in _domains(r)})
    cands += [_cand(target_name, "domain", d) for d in all_domains]
    cands += [
        _cand(target_name, "domain_high_symbol_fan_in",
              scope_json={"domain": d, "min_percentile": 0.90})
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


def run_promotion(
    repo_id: str,
    *,
    store: StorePort,
    learning_port: LearningPort,
    config: pe.PromotionConfig | None = None,
) -> PromotionResult:
    config = config or pe.PromotionConfig()
    all_rows = store.load_production_calibration_rows(repo_id, RISK_BINARY)
    if not all_rows:
        return PromotionResult(
            repo_id=repo_id, promoted=False, snapshot_id=None, fact_ids=[],
            facts_considered=0, facts_promoted=0, facts_vetoed=0,
            veto_reasons=["NO_CALIBRATION_ROWS"],
        )

    by_target: dict[str, list[dict[str, Any]]] = {}
    for r in all_rows:
        by_target.setdefault(r["target_name"], []).append(r)

    fact_dicts: list[dict[str, Any]] = []
    veto_reasons: list[str] = []
    considered = 0
    for target_name, target_rows in by_target.items():
        provider_v, index_v = _extract_provider_provenance(target_rows)
        for candidate in _derive_scope_candidates(target_rows, target_name):
            considered += 1
            gate = pe.evaluate_promotion_gate(candidate, target_rows, config)
            if not gate.promoted:
                if gate.veto_reason:
                    veto_reasons.append(gate.veto_reason)
                continue
            group_rows = [
                r for r in target_rows
                if pe.scope_matches_features(candidate.scope_kind, candidate.scope_value,
                                             candidate.scope_json, r.get("features") or {})
            ]
            value = pe.compute_empirical_value(group_rows)
            fact_dicts.append({
                "target_type": RISK_BINARY,
                "target_name": candidate.target_name,
                "scope_kind": candidate.scope_kind,
                "scope_value": candidate.scope_value,
                "specificity_rank": candidate.specificity_rank,
                "scope_json": candidate.scope_json,
                "fact_json": {
                    "value": value,
                    "weight": 1.0,
                    "sample_size": len(group_rows),
                    "calibration_method": "observed_rate_v1",
                    "calibration_quality": 1.0,
                    "scope_change_count": 0,
                    "provider_version": provider_v,
                    "index_version": index_v,
                },
                "fact_type": "learned_override",
                "status": "active",
                "requires_human_ratification": False,
            })

    if not fact_dicts:
        return PromotionResult(
            repo_id=repo_id, promoted=False, snapshot_id=None, fact_ids=[],
            facts_considered=considered, facts_promoted=0, facts_vetoed=len(veto_reasons),
            veto_reasons=veto_reasons,
        )

    snapshot_metrics = {
        "promotion_reason": "M5d_auto_promotion",
        "repo_id": repo_id,
        "facts_promoted": len(fact_dicts),
        "target_names": sorted({f["target_name"] for f in fact_dicts}),
        "hash_version": 2,
    }
    snapshot_id, fact_ids = learning_port.write_promotion(repo_id, snapshot_metrics, fact_dicts)
    return PromotionResult(
        repo_id=repo_id, promoted=True, snapshot_id=snapshot_id, fact_ids=fact_ids,
        facts_considered=considered, facts_promoted=len(fact_dicts),
        facts_vetoed=len(veto_reasons), veto_reasons=veto_reasons,
    )
