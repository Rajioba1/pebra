"""assessment_builder (Architecture §5/§7, AD-4) — pure factory: AssessmentInput -> scored Assessment.

It receives already-gathered evidence and composes the pure score modules (``score_math``,
``benefit_model``, ``score_normalizer``, ``confidence_gate``). It never calls a port — the controller
is the only orchestrator (plan §8). It sets ``action_status=pending`` (AD-4); terminal states are
written only by the outcome logger.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from typing import Any

from pebra.core import benefit_model, confidence_gate, score_math, score_normalizer
from pebra.core.constants import (
    CODEGRAPH_FILE_METADATA_SCOPE_PENALTY,
    CODEGRAPH_FILE_PARSE_ERROR_PENALTY,
    CODEGRAPH_LARGE_FILE_NODE_COUNT,
    CODEGRAPH_LARGE_FILE_SIZE_BYTES,
    ActionStatus,
)
from pebra.core.models import AssessmentInput

_MIN_CONFIDENCE_FACTOR = 1e-6


@dataclass
class Assessment:
    """In-flight scored bag (§7). Consumed by decision_engine, explanation, guidance."""

    input: AssessmentInput
    scores: dict[str, Any]
    confidence_band: str
    action_status: ActionStatus = ActionStatus.PENDING
    provenance: dict[str, Any] = field(default_factory=dict)


def _effective_threshold(criticality_stage: str, thresholds: dict[str, float]) -> tuple[float, str]:
    """Pick the tighter applicable expected-loss threshold (C3/C4 floor beats global when tighter)."""
    global_t = thresholds.get("max_expected_loss_without_human", 0.45)
    key = "max_expected_loss_without_human"
    eff = global_t
    stage_key = f"{criticality_stage.lower()}_max_expected_loss_without_human"
    if stage_key in thresholds and thresholds[stage_key] < eff:
        eff = thresholds[stage_key]
        key = stage_key
    return eff, key


def _architecture_scope_penalty(inp: AssessmentInput) -> float:
    """Bounded confidence penalty for high-reach structural context.

    Architecture centrality is risk evidence, but not an expected-loss multiplier. It lowers
    scope_control so high-reach edits become less autonomously confident without double-counting
    blast or rewriting the scoring formulas.
    """
    arch = inp.architecture_evidence
    penalty = 0.0
    if arch.god_node_score >= 0.90:
        penalty += 0.08
    elif arch.god_node_score >= 0.75:
        penalty += 0.04
    if arch.cycle_participation:
        penalty += 0.04
    if arch.bridge_centrality >= 0.50:
        penalty += 0.03
    if arch.domain_entrypoint:
        penalty += 0.03
    return min(0.15, penalty)


def _penalized_confidence_factor(current: float, penalty: float) -> float:
    return max(_MIN_CONFIDENCE_FACTOR, current - penalty)


def _verified_event_filter(inp: AssessmentInput) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove only risk events directly disproved by exact, host-produced candidate evidence."""
    verification = inp.candidate_verification
    patch = inp.action.proposed_patch
    bound = bool(
        verification.status == "passed"
        and patch is not None
        and verification.verified_patch_hash
        and verification.verified_patch_hash
        == hashlib.sha256(patch.encode("utf-8")).hexdigest()
    )
    passed = {
        check
        for check in verification.required_checks
        if str(verification.checks.get(check, "")).lower() == "passed"
    }
    if not bound or "public_contract_preserved" not in passed:
        return list(inp.events), []
    disproved = {"public_api_break", "api_contract_break"}
    removed = sorted({str(event.get("event")) for event in inp.events} & disproved)
    return [event for event in inp.events if event.get("event") not in disproved], removed


def build_assessment(inp: AssessmentInput) -> Assessment:
    # --- expected_loss with event-class-aware disutility floor (AD-1) ---
    floored_events: list[dict[str, Any]] = []
    risk_events, verified_risk_events_removed = _verified_event_filter(inp)
    for ev in risk_events:
        disutility, floor_applied = score_math.apply_criticality_floor(
            ev["event"], ev["elicited_disutility"], inp.criticality_value
        )
        floored_events.append(
            {"event": ev["event"], "p_event": ev["p_event"], "disutility": disutility,
             "floor_applied": floor_applied}
        )
    expected_loss, loss_components = score_math.expected_loss(floored_events)

    # --- benefit (AD-28) ---
    bd = inp.benefit_delta_evidence
    benefit_breakdown = benefit_model.resolve_benefit(
        immediate_benefit=inp.immediate_benefit,
        deltas=bd.deltas,
        source_type=bd.source_type,
        future_change_exposure=bd.future_change_exposure,
    )
    if inp.benefit_override is not None:
        # clamp to the unit-utility ceiling so a malformed/out-of-range observed benefit can't inflate
        # expected_utility→RAU without bound (the continuous override is otherwise raw). Safe direction.
        bounded = max(0.0, min(benefit_model.BENEFIT_OVERRIDE_MAX, inp.benefit_override))
        benefit_breakdown = dataclasses.replace(benefit_breakdown, benefit=bounded)
    benefit = benefit_breakdown.benefit

    # --- expected utility, variance, RAU ---
    expected_utility = score_math.expected_utility(
        p_success=inp.p_success,
        benefit=benefit,
        expected_loss=expected_loss,
        review_cost=inp.review_cost,
    )
    # AD-5 precedence: explicit breakdown (1) -> first-order propagation (2) -> cold-start (3).
    # Wire all component variances so precedence 2 is actually reachable through the pipeline:
    # benefit_variance comes from the benefit model; p_success/review_cost variances from evidence.
    variance_breakdown, _total, variance_source = score_normalizer.resolve_utility_variance(
        explicit_breakdown=inp.variance_breakdown,
        benefit=benefit,
        p_success=inp.p_success,
        var_p_success=inp.p_success_variance,
        var_benefit=benefit_breakdown.benefit_variance,
        var_review_cost=inp.review_cost_variance,
    )
    utility_sd = score_math.utility_sd(variance_breakdown)
    rau = score_math.risk_adjusted_utility(expected_utility, utility_sd)

    # --- confidence ---
    confidence_factors = dict(inp.edit_confidence_factors)
    arch_penalty = _architecture_scope_penalty(inp)
    if arch_penalty > 0.0:
        confidence_factors["scope_control"] = _penalized_confidence_factor(
            confidence_factors.get("scope_control", 1.0), arch_penalty
        )
    fanin = inp.fanin_evidence
    if fanin is not None:
        if fanin.graph_file_error_count > 0:
            confidence_factors["evidence_quality"] = _penalized_confidence_factor(
                confidence_factors.get("evidence_quality", 1.0),
                min(0.20, CODEGRAPH_FILE_PARSE_ERROR_PENALTY * fanin.graph_file_error_count),
            )
        file_scope_penalty = 0.0
        if fanin.graph_file_size_bytes >= CODEGRAPH_LARGE_FILE_SIZE_BYTES:
            file_scope_penalty += CODEGRAPH_FILE_METADATA_SCOPE_PENALTY
        if fanin.graph_file_node_count >= CODEGRAPH_LARGE_FILE_NODE_COUNT:
            file_scope_penalty += CODEGRAPH_FILE_METADATA_SCOPE_PENALTY
        if file_scope_penalty > 0.0:
            confidence_factors["scope_control"] = _penalized_confidence_factor(
                confidence_factors.get("scope_control", 1.0), min(0.12, file_scope_penalty)
            )
    edit_conf = score_math.edit_confidence(confidence_factors)
    band = confidence_gate.evaluate(edit_conf, inp.thresholds).band

    # --- risk budget ---
    effective_threshold, budget_key = _effective_threshold(inp.criticality_stage, inp.thresholds)
    risk_budget = score_math.risk_budget_used(expected_loss, effective_threshold)

    sde = inp.symbol_diff_evidence
    if sde.structure_tier == "codegraph_semantic":
        scope_basis = "graph_semantic"
    elif sde.structure_tier == "codegraph_structural":
        scope_basis = "graph_identity"
    elif sde.parsed_patch_available:
        scope_basis = "symbol"
    elif sde.changed_symbols:
        scope_basis = "file_fallback"
    else:
        scope_basis = "unknown_fallback"
    rollup = inp.file_fanin_rollup
    file_fanin_rollup = (
        {
            "percentile": rollup.file_symbol_fanin_rollup_percentile,
            "distinct_caller_count": rollup.distinct_caller_count,
            "max_caller_count": rollup.max_caller_count,
            "symbol_count": rollup.symbol_count,
            "resolution_method": rollup.resolution_method,
            "graph_freshness": rollup.graph_freshness,
            "fallback_reason": rollup.fallback_reason,
            "file_count": rollup.file_count,
            "cumulative_breadth_bonus": rollup.cumulative_breadth_bonus,
        }
        if rollup is not None
        else None
    )
    symbol_fanin = (
        {
            "percentile": fanin.symbol_fan_in_percentile,
            "caller_count": fanin.symbol_caller_count,
            "resolution_method": fanin.resolution_method,
            "graph_freshness": fanin.graph_freshness,
            "fallback_reason": fanin.fallback_reason,
            "owner_kinds": sorted(fanin.owner_kinds),
            "max_owner_span_lines": fanin.max_owner_span_lines,
            "resolved_symbol_count": fanin.resolved_symbol_count,
            "incoming_edge_counts": dict(fanin.incoming_edge_counts),
            "outgoing_edge_counts": dict(fanin.outgoing_edge_counts),
            "modify_impact_count": fanin.modify_impact_count,
            "modify_impact_percentile": fanin.modify_impact_percentile,
            "modify_impact_edge_counts": dict(fanin.modify_impact_edge_counts),
            "modify_transitive_impact_count": fanin.modify_transitive_impact_count,
            "modify_transitive_impact_percentile": fanin.modify_transitive_impact_percentile,
            "modify_transitive_depth_buckets": dict(fanin.modify_transitive_depth_buckets),
            "modify_repo_blast_fraction": fanin.modify_repo_blast_fraction,
            "modify_repo_graph_node_count": fanin.modify_repo_graph_node_count,
            "container_hierarchy_kinds": sorted(fanin.container_hierarchy_kinds),
            "graph_file_size_bytes": fanin.graph_file_size_bytes,
            "graph_file_node_count": fanin.graph_file_node_count,
            "graph_file_error_count": fanin.graph_file_error_count,
            "contract_surface_kind": fanin.contract_surface_kind,
            "is_exported_contract": fanin.is_exported_contract,
            "is_abstract_or_interface_contract": fanin.is_abstract_or_interface_contract,
            "has_signature_metadata": fanin.has_signature_metadata,
            # Graph identity for dashboard hotspot replay: the resolved owners' qualified names +
            # files, so a stored assessment can be mapped back onto graph nodes later (the same
            # qualified-name identity the verify path re-resolves by). Aggregate counts can't do that.
            "resolved_qualified_names": list(fanin.resolved_qualified_names),
            "resolved_file_paths": list(fanin.resolved_file_paths),
            "changed_owner_edge_count": fanin.changed_owner_edge_count,
        }
        if fanin is not None
        else None
    )
    scores: dict[str, Any] = {
        "expected_loss": expected_loss,
        "verified_risk_events_removed": verified_risk_events_removed,
        "loss_components": loss_components,
        "benefit": benefit,
        "benefit_breakdown": benefit_breakdown,
        "benefit_file_deltas": {path: dict(values) for path, values in bd.file_deltas.items()},
        "expected_utility": expected_utility,
        "utility_sd": utility_sd,
        "variance_breakdown": variance_breakdown,
        "variance_source": variance_source,
        "rau": rau,
        "edit_confidence": edit_conf,
        "edit_confidence_factors": confidence_factors,
        "effective_threshold": effective_threshold,
        "budget_threshold_key": budget_key,
        "risk_budget_used": risk_budget,
        "candidate_aggregate": dataclasses.asdict(inp.candidate_aggregate_evidence),
        "criticality_stage": inp.criticality_stage,
        "criticality_value": inp.criticality_value,
        "symbol_scope_evidence": {
            "scope_basis": scope_basis,
            "changed_symbols": list(sde.changed_symbols),
            "max_change_kind": sde.max_change_kind,
            "visibility": sde.visibility,
            "symbol_fan_in_percentile": sde.symbol_fan_in_percentile,
            "symbol_fanin": symbol_fanin,
            "consequential_symbol_changed": sde.consequential_symbol_changed,
            "consequence_reason": list(sde.consequence_reason),
            "file_operation_kind": sde.file_operation_kind,
            "file_operation_paths": list(sde.file_operation_paths),
            "file_fanin_rollup": file_fanin_rollup,
            "fallback_reason": sde.fallback_reason,
            # which structural tier produced this classification (python_ast | codegraph_semantic |
            # codegraph_structural | unavailable) — surfaced so guidance can be honest.
            "structure_tier": sde.structure_tier,
        },
        "candidate_verification": {
            "status": inp.candidate_verification.status,
            "checks": dict(inp.candidate_verification.checks),
            "required_checks": list(inp.candidate_verification.required_checks),
            "domain": inp.candidate_verification.domain,
            "reason": inp.candidate_verification.reason,
            # Record which patch the verification was bound to, so the manifest audit shows the
            # gate-7 patch-binding input (see decision_engine.candidate_patch_hash).
            "verified_patch_hash": inp.candidate_verification.verified_patch_hash,
        },
    }
    return Assessment(
        input=inp,
        scores=scores,
        confidence_band=band,
        action_status=ActionStatus.PENDING,
        provenance={"provider": "pebra", "source_type": "derived"},
    )
