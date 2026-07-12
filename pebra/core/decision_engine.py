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

import hashlib
from dataclasses import asdict
from collections.abc import Sequence
from typing import Any

from pebra.core.assessment_builder import Assessment
from pebra.core.constants import (
    ActionStatus,
    Decision,
    GraphFreshness,
    RiskMode,
    UNCERTAIN_STRUCTURE_TIERS,
)
from pebra.core.graph_trust import is_trusted_fanin
from pebra.core.models import AssessmentResult
from pebra.core.patch_paths import touched_files


def candidate_patch_hash(patch: str) -> str:
    """The wire convention binding a candidate verification to the patch it ran against:
    sha256 hexdigest of the exact UTF-8 patch text, no normalization (any drift breaks the bind,
    which fails safe to REVISE_SAFER). The candidate-verifier adapter MUST produce this same digest
    when populating ``CandidateVerificationEvidence.verified_patch_hash``."""
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()

_SENSITIVE_STAGES = {"C3", "C4"}
_HARD_TERMINAL_EVENTS = frozenset(
    {"security_sensitive_change", "external_state_damage", "migration_failure"}
)
_STRUCTURAL_RISK_EVENTS = frozenset(
    {
        "public_api_break",
        "dependency_break",
        "api_contract_break",
        "route_behavior_break",
        "tool_schema_break",
        "response_shape_mismatch",
        "consumer_shape_mismatch",
    }
)
_DEFAULT_REPO_BLAST_FRACTION_INSPECT_THRESHOLD = 0.40
_DEFAULT_REPO_BLAST_MIN_REPO_NODE_COUNT = 50


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
        "benefit_breakdown": asdict(s["benefit_breakdown"]),
        "benefit_file_deltas": s["benefit_file_deltas"],
        "candidate_aggregate": s["candidate_aggregate"],
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


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _revision_exhausted(thresholds: dict[str, Any]) -> bool:
    attempt = _as_int(thresholds.get("revise_safer_attempt", 0), 0)
    cap = _as_int(thresholds.get("max_revise_safer_attempts", 1), 1)
    return cap <= 0 or attempt >= cap


def _has_narrowing_headroom(assessment: Assessment) -> bool:
    action = assessment.input.action
    sse = assessment.scores["symbol_scope_evidence"]
    file_op = str(sse.get("file_operation_kind", "NONE"))
    changed_symbols = sse.get("changed_symbols") or []
    expected_files = action.expected_files or []
    events = {str(c.get("event")) for c in assessment.scores["loss_components"]}
    if file_op in {"DELETE", "RENAME", "MOVE"}:
        return True
    # Public contract/API breaks can be revised without changing files or owners: preserve the
    # existing interface, add a wrapper/default, or move the risky requirement behind a compatible
    # route. Candidate verification then decides whether the revised route is good enough.
    if (
        events & {"api_contract_break", "public_api_break"}
        and str(sse.get("max_change_kind", "")).upper() == "CONTRACT"
        and (sse.get("consequential_symbol_changed") or sse.get("visibility") == "public_api")
    ):
        return True
    # Non-Python hosts may not provide semantic SymbolDiff rows on the assess path, but CodeGraph still
    # parses the unified diff hunks and resolves the touched old-side line ranges to owner nodes. Treat
    # that parsed, multi-owner graph scope as narrowing headroom; do not revise every UNKNOWN one-file
    # edit just because it has an expected file.
    if action.proposed_patch and file_op == "NONE" and not changed_symbols and sse.get("max_change_kind") == "UNKNOWN":
        fanin = sse.get("symbol_fanin") if isinstance(sse.get("symbol_fanin"), dict) else {}
        return int(fanin.get("resolved_symbol_count") or 0) > 1
    return len(changed_symbols) > 1 or len(expected_files) > 1


def _should_revise_safer(assessment: Assessment) -> bool:
    """True when a risky route is plausibly narrowable before human escalation.

    This is deliberately structural, not semantic: PEBRA does not invent the safer patch. It only
    detects that the current patch has scope to narrow (multi-symbol/file or destructive operation)
    and that the risk is dependency/API/contract shaped rather than a hard terminal class.
    """
    thresholds = assessment.input.thresholds
    if not thresholds.get("revise_safer_enabled", True):
        return False
    if _revision_exhausted(thresholds):
        return False
    # Eligibility asks whether preserving the task goal has any value, not whether THIS risky route
    # already has positive net utility. Expected utility includes the loss that triggered this gate;
    # using it here makes increasing risk suppress the very safer-route search this decision exists
    # to request. Candidate verification can prove a revised route safe, but cannot manufacture value.
    if assessment.scores["benefit"] <= 0:
        return False
    events = {str(c.get("event")) for c in assessment.scores["loss_components"]}
    if events & _HARD_TERMINAL_EVENTS:
        return False
    if not (events & _STRUCTURAL_RISK_EVENTS):
        return False
    return _has_narrowing_headroom(assessment)


def _revise_candidate_decision(
    assessment: Assessment,
    gates_fired: list[dict[str, Any]],
    *,
    source_gate: int,
) -> tuple[Decision, bool, int]:
    verification = assessment.input.candidate_verification
    status = verification.status
    required_checks = list(verification.required_checks)
    missing_or_failed = [
        check
        for check in required_checks
        if str(verification.checks.get(check, "")).lower() != "passed"
    ]
    # A passed proof is honored ONLY when it is BOUND to the exact patch under assessment: the action
    # must carry a candidate patch AND verified_patch_hash must pin it. An unbound "passed" (no patch
    # to bind, or absent/mismatched hash) is a stale/forged/replayed proof and fails safe to
    # REVISE_SAFER — never PROCEED. Keying the bind on proposed_patch being *present* (not on the
    # caller choosing to omit it) closes the omit-the-patch replay bypass: gate 7 is reachable via
    # structural narrowing headroom (expected_files/changed_symbols) with no patch at all.
    proposed_patch = assessment.input.action.proposed_patch
    if status == "passed" and required_checks and not missing_or_failed:
        bound = (
            proposed_patch is not None
            and bool(verification.verified_patch_hash)
            and verification.verified_patch_hash == candidate_patch_hash(proposed_patch)
        )
        if not bound:
            gates_fired.append({
                "gate": 7,
                "name": "candidate_verification_patch_mismatch",
                "domain": verification.domain,
                "required_checks": required_checks,
                "reason": (
                    "a passed candidate verification must be bound to the candidate patch under "
                    "assessment via verified_patch_hash; unbound/absent/mismatched proof is not honored"
                ),
            })
            return Decision.REVISE_SAFER, False, source_gate
        gates_fired.append({
            "gate": 7,
            "name": "candidate_verification_passed",
            "domain": verification.domain,
            "checks": dict(verification.checks),
            "required_checks": required_checks,
        })
        return Decision.PROCEED, assessment.scores["criticality_stage"] in _SENSITIVE_STAGES, 7
    if status in {"passed", "failed", "unavailable"}:
        gates_fired.append({
            "gate": 7,
            "name": "candidate_verification_not_passed",
            "status": status,
            "domain": verification.domain,
            "checks": dict(verification.checks),
            "required_checks": required_checks,
            "missing_or_failed_checks": missing_or_failed,
            "reason": verification.reason,
        })
        return Decision.REVISE_SAFER, False, source_gate
    gates_fired.append({"gate": 6, "name": "revise_safer"})
    return Decision.REVISE_SAFER, False, source_gate


def _revision_completeness_issue(assessment: Assessment) -> dict[str, Any] | None:
    evidence = assessment.input.revision_completeness_evidence
    if not evidence.is_revision:
        return None
    if not evidence.origin_available:
        return {
            "gate": 9,
            "name": "revision_envelope_unavailable",
            "reason": evidence.fallback_reason or "origin revision envelope unavailable",
        }
    if not evidence.missing_files and not evidence.missing_public_symbols:
        return None
    return {
        "gate": 9,
        "name": "revision_envelope_incomplete",
        "missing_files": list(evidence.missing_files),
        "missing_public_symbols": list(evidence.missing_public_symbols),
    }


def _task_obligations_issue(assessment: Assessment) -> dict[str, Any] | None:
    obligations = assessment.input.task_obligations
    if not (
        obligations.required_files
        or obligations.required_symbols
        or obligations.required_checks
    ):
        return None
    action_files = set(touched_files(assessment.input.action.proposed_patch or ""))
    changed_symbols = set(assessment.input.symbol_diff_evidence.changed_symbols)
    missing_files = sorted(
        path for path in obligations.required_files
        if str(path).replace("\\", "/") not in action_files
    )
    missing_symbols = sorted(set(obligations.required_symbols) - changed_symbols)
    verification = assessment.input.candidate_verification
    proposed_patch = assessment.input.action.proposed_patch
    proof_bound = (
        verification.status == "passed"
        and proposed_patch is not None
        and bool(verification.verified_patch_hash)
        and verification.verified_patch_hash == candidate_patch_hash(proposed_patch)
    )
    missing_checks = sorted(
        check for check in obligations.required_checks
        if not proof_bound or str(verification.checks.get(check, "")).lower() != "passed"
    )
    if not (missing_files or missing_symbols or missing_checks):
        return None
    return {
        "gate": 15,
        "name": "task_obligations_incomplete",
        "missing_files": missing_files,
        "missing_symbols": missing_symbols,
        "missing_or_unverified_checks": missing_checks,
    }


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
    if is_trusted_fanin(ev):
        return {}
    return {
        "resolution_method": ev.resolution_method,
        "graph_freshness": ev.graph_freshness,
        "reason": ev.fallback_reason or "graph evidence unavailable",
    }


def _repo_blast_advisory(inp: Any) -> dict[str, Any]:
    ev = inp.fanin_evidence
    if not is_trusted_fanin(ev):
        return {}
    if ev.modify_repo_blast_fraction <= 0.0 or ev.modify_repo_graph_node_count <= 0:
        return {}
    return {
        "modify_repo_blast_fraction": ev.modify_repo_blast_fraction,
        "repo_node_count": ev.modify_repo_graph_node_count,
        "affected_node_count": ev.modify_transitive_impact_count,
        "depth": 3,
    }


def _repo_blast_gate(inp: Any) -> dict[str, Any]:
    advisory = _repo_blast_advisory(inp)
    if not advisory or not inp.thresholds.get("inspect_on_large_repo_blast", True):
        return {}
    min_nodes = int(
        inp.thresholds.get(
            "repo_blast_min_repo_node_count", _DEFAULT_REPO_BLAST_MIN_REPO_NODE_COUNT
        )
    )
    threshold = float(
        inp.thresholds.get(
            "repo_blast_fraction_inspect_threshold",
            _DEFAULT_REPO_BLAST_FRACTION_INSPECT_THRESHOLD,
        )
    )
    if advisory["repo_node_count"] < min_nodes:
        return {}
    if advisory["modify_repo_blast_fraction"] < threshold:
        return {}
    return {
        "gate": 14,
        "name": "large_repo_blast_fraction",
        **advisory,
        "threshold": threshold,
        "min_repo_node_count": min_nodes,
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
    repo_blast = _repo_blast_gate(assessment.input)
    repo_blast_advisory = _repo_blast_advisory(assessment.input)
    revision_issue = _revision_completeness_issue(assessment)
    obligations_issue = _task_obligations_issue(assessment)
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
            provenance={
                "provider": "pebra",
                "source_type": "derived",
                **({"repo_blast": repo_blast_advisory} if repo_blast_advisory else {}),
            },
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

    # The codegraph_structural tier is COARSE: it proves an owner was touched, not what changed inside
    # it, so it is still uncertain and must inherit UNKNOWN's escalation rather than suppress it —
    # otherwise reclassifying UNKNOWN -> BEHAVIORAL for an internal owner would silently drop this C4
    # gate (the tier would LOWER conservatism). Keeps the tier monotonic: it can only ADD severity.
    consequential_or_unknown = (
        sse["consequential_symbol_changed"]
        or sse["max_change_kind"] == "UNKNOWN"
        or sse.get("structure_tier") in UNCERTAIN_STRUCTURE_TIERS
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
        if _should_revise_safer(assessment):
            provisional, requires_confirmation, fired_gate = _revise_candidate_decision(
                assessment, gates_fired, source_gate=3
            )
        elif s["expected_utility"] < 0:
            provisional, fired_gate = Decision.REJECT, 3
        else:
            provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 3
        gates_fired.append({"gate": 3, "name": "expected_loss_over_threshold",
                            "expected_loss": s["expected_loss"],
                            "threshold": s["effective_threshold"]})
    # --- Gate 4: RAU < 0 ---
    elif s["rau"] < 0:
        if _should_revise_safer(assessment):
            provisional, requires_confirmation, fired_gate = _revise_candidate_decision(
                assessment, gates_fired, source_gate=4
            )
        elif t.get("ask_on_negative_rau", True):
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
    # --- Gate 9: a safer revision may not win by silently dropping origin obligations ---
    elif revision_issue:
        verified_decision: Decision | None = None
        if assessment.input.candidate_verification.status == "passed":
            verified_decision, _, _ = _revise_candidate_decision(
                assessment, gates_fired, source_gate=9
            )
        if verified_decision is Decision.PROCEED:
            provisional, fired_gate = Decision.PROCEED, 7
            requires_confirmation = stage in _SENSITIVE_STAGES
        elif not _revision_exhausted(t):
            provisional, fired_gate = Decision.REVISE_SAFER, 9
        else:
            provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 9
        gates_fired.append(revision_issue)
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
    # --- Gate 14: large repo-relative CodeGraph blast guardrail ---
    # This is an uncalibrated absolute-scale guardrail, not expected-loss math. It only downgrades a
    # would-be proceed to inspect_first when a trusted graph says the edit reaches a very large share
    # of the indexed repo and the repo is large enough for the fraction to be meaningful.
    elif repo_blast:
        provisional, fired_gate = Decision.INSPECT_FIRST, 14
        gates_fired.append(repo_blast)
    # --- Gate 11: proceed ---
    else:
        provisional, fired_gate = Decision.PROCEED, 11
        # A C4 edit only reaches gate 11 when it is verified COSMETIC / safe TEST_ONLY (gate 2
        # handles consequential C4), but still remains sensitive enough to require confirmation.
        requires_confirmation = stage in _SENSITIVE_STAGES

    # --- Gate 15: host-declared task completeness ---
    # This may only downgrade a would-be proceed. Risk gates remain authoritative, and checks only
    # count when their passed evidence is bound to the exact candidate patch.
    if provisional is Decision.PROCEED and obligations_issue:
        provisional, requires_confirmation, fired_gate = Decision.ASK_HUMAN, True, 15
        gates_fired.append(obligations_issue)
    elif fired_gate == 11:
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
    if repo_blast and not any(g.get("gate") == 14 for g in gates_fired):
        gates_fired.append({"advisory": True, **repo_blast})

    # --- Gate 10: authorized sanction resolution (AD-26) ---
    # NOTE: gates 12 (stale arch map) and 13 (codegraph evidence-validity) both yield INSPECT_FIRST,
    # never ASK_HUMAN/REJECT, so the provisional guard below already excludes them — a sanction can
    # never convert an evidence-validity gate.
    sanction = assessment.input.sanction
    elevated = provisional in {Decision.INSPECT_FIRST, Decision.TEST_FIRST, Decision.REVISE_SAFER} and stage in _SENSITIVE_STAGES
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
