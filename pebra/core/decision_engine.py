"""decision_engine (Architecture §6/§8) — the SOLE gate authority. Pure, stdlib only.

Runs the ordered gate sequence over a scored ``Assessment`` and returns an ``AssessmentResult``. The
first matching risk gate sets a provisional decision; an authorized sanction (pre-fetched into
``AssessmentInput`` — the engine never calls a port, AD-26) may then convert a risk-threshold
ask_human/reject from gates 2/3/4 into a controlled-high-risk proceed. It never overrides a gate-1
policy violation.

The double-count guard holds: criticality already fed the disutility floor + threshold (in the
builder); it never enters p_event here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pebra.core.assessment_builder import Assessment
from pebra.core.constants import ActionStatus, Decision, GraphFreshness, RiskMode
from pebra.core.models import AssessmentResult

_SENSITIVE_STAGES = {"C3", "C4"}


def _flatten_scores(a: Assessment) -> dict[str, Any]:
    s = a.scores
    return {
        "expected_loss": s["expected_loss"],
        "expected_utility": s["expected_utility"],
        "utility_sd": s["utility_sd"],
        "rau": s["rau"],
        "edit_confidence": s["edit_confidence"],
        "risk_budget_used": s["risk_budget_used"],
        "effective_threshold": s["effective_threshold"],
        "budget_threshold_key": s["budget_threshold_key"],
        "benefit": s["benefit"],
        "criticality_stage": s["criticality_stage"],
        "criticality_value": s["criticality_value"],
        "confidence_band": a.confidence_band,
        "loss_components": s["loss_components"],
        "symbol_scope_evidence": s["symbol_scope_evidence"],
        "variance_breakdown": s["variance_breakdown"],
    }


def _risk_mode(decision: Decision, stage: str, *, controlled: bool, elevated: bool) -> RiskMode:
    if controlled:
        return RiskMode.CONTROLLED_HIGH_RISK
    if elevated:
        return RiskMode.ELEVATED_REVIEW
    if stage in _SENSITIVE_STAGES:
        return RiskMode.SENSITIVE_CONTEXT
    return RiskMode.NORMAL


def _graph_evidence(blast: Any) -> dict[str, Any]:
    """Surface blast graph incompleteness (3c/3d) for rendering. Empty when the graph is fully
    resolved, so a clean assessment carries no uncertainty noise (and the worked example is unchanged)."""
    if blast.graph_uncertainty_score <= 0.0:
        return {}
    return {
        "score": blast.graph_uncertainty_score,
        "reason": blast.graph_uncertainty_reason,
        "unresolved_import_count": blast.unresolved_import_count,
        "dynamic_import_count": blast.dynamic_import_count,
        "wildcard_import_count": blast.wildcard_import_count,
        "missing_file_count": blast.missing_file_count,
        "parse_error_count": blast.parse_error_count,
        "unresolved_imports": list(blast.unresolved_imports),
        "dynamic_imports": list(blast.dynamic_imports),
        "wildcard_imports": list(blast.wildcard_imports),
        "missing_files": list(blast.missing_files),
        "parse_error_files": list(blast.parse_error_files),
    }


def _fanin_validity(inp: Any) -> dict[str, Any]:
    """Codegraph evidence-validity advisory (Gate 13). Empty unless the graph engine is REQUIRED
    (threshold ``require_graph``) AND the per-symbol fan-in evidence is untrusted — absent, stale,
    worktree-mismatched, ambiguous, or unresolved. A 0.0 percentile from such a state is the ABSENCE of
    evidence, not 'low fan-in = safe', so it must downgrade a would-be proceed to inspect_first with an
    actionable remediation (carried in ``reason``)."""
    if not inp.thresholds.get("require_graph", False):
        return {}
    ev = inp.fanin_evidence
    if ev is None:
        # Required, but no provider produced fan-in evidence at all (e.g. fanin_provider not wired).
        # That is the ABSENCE of required evidence -> fail CLEAR (Gate 13), never silently fail open.
        return {
            "resolution_method": "unresolved",
            "graph_freshness": "unknown",
            "reason": "graph engine required but no fan-in evidence was produced; run: pebra setup-graph",
        }
    trusted = ev.graph_freshness == "fresh" and ev.resolution_method in ("location", "name_fallback")
    if trusted:
        return {}
    return {
        "resolution_method": ev.resolution_method,
        "graph_freshness": ev.graph_freshness,
        "reason": ev.fallback_reason or "graph evidence unavailable",
    }


def decide(
    assessment: Assessment, *, policy_violations: Sequence[str] = ()
) -> AssessmentResult:
    s = assessment.scores
    t = assessment.input.thresholds
    stage = s["criticality_stage"]
    sse = s["symbol_scope_evidence"]
    ge = _graph_evidence(assessment.input.blast_evidence)
    cg = _fanin_validity(assessment.input)
    flat = _flatten_scores(assessment)

    def _result(
        decision: Decision,
        *,
        requires_confirmation: bool,
        risk_mode: RiskMode,
        gates_fired: list[dict[str, Any]],
        high_risk_triggers: list[dict[str, Any]] | None = None,
        decision_reason: str = "",
    ) -> AssessmentResult:
        return AssessmentResult(
            recommended_decision=decision,
            requires_confirmation=requires_confirmation,
            action_status=ActionStatus.PENDING,
            risk_mode=risk_mode,
            scores=flat,
            repo_id=assessment.input.repo_id,
            repo_root=assessment.input.repo_root,
            gates_fired=gates_fired,
            high_risk_triggers=high_risk_triggers or [],
            symbol_scope_evidence=sse,
            graph_evidence=ge,
            fanin_validity=cg,
            provenance={"provider": "pebra", "source_type": "derived"},
            decision_reason=decision_reason,
        )

    # --- Gate 1: policy violation (cannot be overridden by a sanction) ---
    if policy_violations:
        return _result(
            Decision.REJECT,
            requires_confirmation=False,
            risk_mode=_risk_mode(Decision.REJECT, stage, controlled=False, elevated=False),
            gates_fired=[{"gate": 1, "name": "policy_violation", "detail": list(policy_violations)}],
            high_risk_triggers=[
                {"trigger_id": "pol_001", "risk_class": "policy_violation",
                 "trigger_source": "policy", "severity": "critical",
                 "decision_effect": "reject"}
            ],
            decision_reason=f"Policy violation: {', '.join(policy_violations)}.",
        )

    gates_fired: list[dict[str, Any]] = []
    provisional: Decision | None = None
    requires_confirmation = False
    fired_gate: int | None = None

    consequential_or_unknown = (
        sse["consequential_symbol_changed"] or sse["max_change_kind"] == "UNKNOWN"
    )

    # --- Gate 2: C4 always ask_human on consequential/unknown symbol change ---
    if (
        stage == "C4"
        and t.get("c4_always_ask_human", True)
        and consequential_or_unknown
    ):
        provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 2
        gates_fired.append({"gate": 2, "name": "c4_consequential_ask_human"})
    # --- Gate 3: expected_loss over effective threshold ---
    elif s["expected_loss"] > s["effective_threshold"]:
        if s["expected_utility"] < 0:
            provisional, fired_gate = Decision.REJECT, 3
        else:
            provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 3
        gates_fired.append({"gate": 3, "name": "expected_loss_over_threshold",
                            "expected_loss": s["expected_loss"],
                            "threshold": s["effective_threshold"]})
    # --- Gate 4: RAU < 0 ---
    elif s["rau"] < 0:
        if t.get("ask_on_negative_rau", True):
            provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 4
        else:
            provisional, fired_gate = Decision.REJECT, 4
        gates_fired.append({"gate": 4, "name": "negative_rau", "rau": s["rau"]})
    # --- Gate 5: utility_sd too wide while EU positive ---
    elif (
        s["utility_sd"] > t.get("max_utility_sd_without_human", 0.20)
        and s["expected_utility"] > 0
    ):
        provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 5
        gates_fired.append({"gate": 5, "name": "utility_sd_over_limit", "utility_sd": s["utility_sd"]})
    # --- Gate 8: low edit confidence ---
    elif assessment.confidence_band == "low":
        provisional, fired_gate = Decision.INSPECT_FIRST, 8
        gates_fired.append({"gate": 8, "name": "low_edit_confidence",
                            "edit_confidence": s["edit_confidence"]})
    # --- Evidence-validity gate (AD-22): unresolved-stale architecture map ---
    # Placed last before proceed so it only downgrades a would-be proceed — it never preempts a more
    # severe gate above. STALE means the map was stale AND the adapter's rebuild failed, so PEBRA
    # can't trust the blast/criticality evidence those gates relied on. fresh/rebuilt/unknown don't fire.
    elif (
        assessment.input.architecture_evidence.graph_freshness is GraphFreshness.STALE
        and t.get("inspect_on_stale_arch_map", True)
    ):
        provisional, fired_gate = Decision.INSPECT_FIRST, 12
        gates_fired.append({"gate": 12, "name": "stale_architecture_map"})
    # --- Gate 13: codegraph evidence-validity (required graph engine, untrusted fan-in) ---
    # Same family as Gate 12: only downgrades a would-be proceed to inspect_first; never preempts a
    # more severe gate above. Carries the actionable remediation so the user knows WHAT to run.
    elif cg:
        provisional, fired_gate = Decision.INSPECT_FIRST, 13
        gates_fired.append({"gate": 13, "name": "fanin_evidence_invalid", **cg})
    # --- Gate 11: proceed ---
    else:
        provisional, fired_gate = Decision.PROCEED, 11
        # A C4 edit only reaches gate 11 when it is verified COSMETIC / safe TEST_ONLY (gate 2
        # handles consequential C4), but still remains sensitive enough to require confirmation.
        requires_confirmation = stage in _SENSITIVE_STAGES
        gates_fired.append({"gate": 11, "name": "proceed"})

    # Evidence-validity observability: record a stale architecture map even when a higher gate drove
    # the decision (so the audit trail shows the evidence was untrustworthy, not just the headline gate).
    if assessment.input.architecture_evidence.graph_freshness is GraphFreshness.STALE and not any(
        g.get("gate") == 12 for g in gates_fired
    ):
        gates_fired.append({"gate": 12, "name": "stale_architecture_map", "advisory": True})

    # Same observability for codegraph evidence-validity: record it even when a higher gate decided,
    # so the audit trail shows the fan-in evidence was untrustworthy, not just the headline gate.
    if cg and not any(g.get("gate") == 13 for g in gates_fired):
        gates_fired.append({"gate": 13, "name": "fanin_evidence_invalid", "advisory": True, **cg})

    # --- Gate 10: authorized sanction resolution (AD-26) ---
    # NOTE: gates 12 (stale arch map) and 13 (codegraph evidence-validity) both yield INSPECT_FIRST,
    # never ASK_HUMAN/REJECT, so the provisional guard below already excludes them — a sanction can
    # never convert an evidence-validity gate.
    sanction = assessment.input.sanction
    elevated = provisional in {Decision.INSPECT_FIRST, Decision.TEST_FIRST} and stage in _SENSITIVE_STAGES
    if (
        sanction
        and provisional in {Decision.ASK_HUMAN, Decision.REJECT}
        and fired_gate in {2, 3, 4}
        and sanction.get("valid")
        and sanction.get("pre_edit_authorization_controls_satisfied")
        and fired_gate in set(sanction.get("converts_gates", []))
    ):
        gates_fired.append({"gate": 10, "name": "sanction_resolution",
                            "converted_from": provisional.value})
        return _result(
            Decision.PROCEED,
            requires_confirmation=True,
            risk_mode=_risk_mode(Decision.PROCEED, stage, controlled=True, elevated=False),
            gates_fired=gates_fired,
            high_risk_triggers=list(sanction.get("high_risk_triggers", [])),
            decision_reason="Controlled high-risk proceed: authorized sanction converted a "
            f"gate-{fired_gate} {provisional.value}.",
        )

    assert provisional is not None
    return _result(
        provisional,
        requires_confirmation=requires_confirmation,
        risk_mode=_risk_mode(provisional, stage, controlled=False, elevated=elevated),
        gates_fired=gates_fired,
        decision_reason=f"Decision {provisional.value} from gate {fired_gate}.",
    )
