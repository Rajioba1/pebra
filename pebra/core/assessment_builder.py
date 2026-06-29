"""assessment_builder (Architecture §5/§7, AD-4) — pure factory: AssessmentInput -> scored Assessment.

It receives already-gathered evidence and composes the pure score modules (``score_math``,
``benefit_model``, ``score_normalizer``, ``confidence_gate``). It never calls a port — the controller
is the only orchestrator (plan §8). It sets ``action_status=pending`` (AD-4); terminal states are
written only by the outcome logger.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from pebra.core import benefit_model, confidence_gate, score_math, score_normalizer
from pebra.core.constants import ActionStatus
from pebra.core.models import AssessmentInput


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


def build_assessment(inp: AssessmentInput) -> Assessment:
    # --- expected_loss with event-class-aware disutility floor (AD-1) ---
    floored_events: list[dict[str, Any]] = []
    for ev in inp.events:
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
        benefit_breakdown = dataclasses.replace(
            benefit_breakdown, benefit=inp.benefit_override
        )
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
        confidence_factors["scope_control"] = max(
            0.0, confidence_factors.get("scope_control", 1.0) - arch_penalty
        )
    edit_conf = score_math.edit_confidence(confidence_factors)
    band = confidence_gate.evaluate(edit_conf, inp.thresholds).band

    # --- risk budget ---
    effective_threshold, budget_key = _effective_threshold(inp.criticality_stage, inp.thresholds)
    risk_budget = score_math.risk_budget_used(expected_loss, effective_threshold)

    sde = inp.symbol_diff_evidence
    if sde.parsed_patch_available:
        scope_basis = "symbol"
    elif sde.changed_symbols:
        scope_basis = "file_fallback"
    else:
        scope_basis = "unknown_fallback"
    scores: dict[str, Any] = {
        "expected_loss": expected_loss,
        "loss_components": loss_components,
        "benefit": benefit,
        "benefit_breakdown": benefit_breakdown,
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
        "criticality_stage": inp.criticality_stage,
        "criticality_value": inp.criticality_value,
        "symbol_scope_evidence": {
            "scope_basis": scope_basis,
            "changed_symbols": list(sde.changed_symbols),
            "max_change_kind": sde.max_change_kind,
            "visibility": sde.visibility,
            "symbol_fan_in_percentile": sde.symbol_fan_in_percentile,
            "consequential_symbol_changed": sde.consequential_symbol_changed,
            "consequence_reason": list(sde.consequence_reason),
            "file_operation_kind": sde.file_operation_kind,
            "file_operation_paths": list(sde.file_operation_paths),
            "fallback_reason": sde.fallback_reason,
        },
    }
    return Assessment(
        input=inp,
        scores=scores,
        confidence_band=band,
        action_status=ActionStatus.PENDING,
        provenance={"provider": "pebra", "source_type": "derived"},
    )
