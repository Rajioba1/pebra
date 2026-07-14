"""Data model for the agent-A/B experiment. Pure stdlib, frozen dataclasses.

Kept deliberately free of any adapter type (e.g. DotNetBuildResult): the runner translates the
build result into the plain ``build_*`` fields below so the scoring layer stays a pure, testable
"ruler" with no e2e-adapter or pebra dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- the two arms -----------------------------------------------------------------------------

ARM_CONTROL = "control"
ARM_TREATMENT = "treatment"

# ---- multi-arm ASSAY arms (additive; the legacy 2-arm control/treatment path is untouched) -----
ARM_SHAM = "sham"                        # baseline / placebo (generic advisory)
ARM_BLAST_RADIUS = "blast_radius"        # CTXO-style graph-guidance diagnostic (NOT literal CTXO)
ARM_ENFORCED_CONTROL = "enforced_control"  # sensitivity control: write gate blocks known-risky edits
ARM_PEBRA = "pebra"                      # experimental treatment
ARM_ORACLE_POSITIVE = "oracle_positive"  # endpoint floor: correct fix pre-applied before the agent runs
ARM_PEBRA_GRAPH_REPAIR = "pebra_graph_repair"  # PEBRA + repair context + host candidate verification
ARM_PEBRA_HUMAN_REVIEW = "pebra_human_review"  # repair + pre-registered host approval policy

# The full assay arm universe. Every arm-membership frozenset in run_pair.py is validated against this
# at import (fail loud on a missing/unregistered arm) so a mis-wired arm can never run as a silent
# placebo. Legacy ARM_CONTROL/ARM_TREATMENT are NOT part of the assay path and stay out of this set.
ALL_ASSAY_ARMS: tuple[str, ...] = (
    ARM_SHAM,
    ARM_BLAST_RADIUS,
    ARM_ENFORCED_CONTROL,
    ARM_PEBRA,
    ARM_ORACLE_POSITIVE,
    ARM_PEBRA_GRAPH_REPAIR,
    ARM_PEBRA_HUMAN_REVIEW,
)

# Arms backed by the real advisory backend/protocol. Centralized so adding a future real-advisory arm
# cannot silently get the placebo protocol while still receiving real gate/advisory plumbing.
REAL_ADVISORY_ARMS = frozenset({
    ARM_TREATMENT, ARM_PEBRA, ARM_PEBRA_GRAPH_REPAIR, ARM_PEBRA_HUMAN_REVIEW,
})

# Every tool that can mutate tracked source. Scoring, adherence, traces, and the runner must share
# this vocabulary so adding a surgical editor cannot create an unscored write path.
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "apply_patch"})

# Pre-registered assay verdicts (checked in order; see metrics/assay_interpret.py). Efficacy sample
# requirements belong to each run's declared claim design; there is no universal pair-count constant.
VERDICT_DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
VERDICT_NO_HEADROOM = "INVALID_NO_HEADROOM"
VERDICT_ASSAY_INSENSITIVE = "INVALID_ASSAY_INSENSITIVE"
VERDICT_INSUFFICIENT_DATA = "INVALID_INSUFFICIENT_DATA"
VERDICT_PEBRA_INFERIOR = "PEBRA_INFERIOR"
VERDICT_PEBRA_PARTIAL = "PEBRA_EFFICACY_PARTIAL"
VERDICT_PEBRA_SUPERIOR = "PEBRA_SUPERIOR"
VERDICT_PEBRA_HARM_ONLY = "PEBRA_HARM_AVOIDANCE_ONLY"
# The graph-repair arm verdict tier (only reached when ARM_PEBRA_GRAPH_REPAIR is in the run).
VERDICT_PEBRA_GRAPH_REPAIR_PARTIAL = "PEBRA_GRAPH_REPAIR_PARTIAL"
VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR = "PEBRA_GRAPH_REPAIR_SUPERIOR"
VERDICT_PEBRA_GRAPH_REPAIR_HARM_ONLY = "PEBRA_GRAPH_REPAIR_HARM_AVOIDANCE_ONLY"


# ---- task corpus ------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpec:
    """One experimental task. ``description``/``target_hints`` are AGENT-FACING; the remaining
    fields are the HIDDEN oracle label the agent never sees."""

    task_id: str
    description: str                    # agent-facing (from tasks.jsonl)
    target_hints: tuple[str, ...]       # agent-facing repo-relative hints
    harm_label: str                     # hidden: "risky" | "safe"
    expected_edit_scope: tuple[str, ...]  # hidden: rel paths the task legitimately touches
    harm_type: str                      # hidden: "build_failure" | "scope_drift" | "test_failure" | "none"
    oracle_build_must_fail: bool        # hidden: the intended change should break `dotnet build`
    evaluator_test_project: str | None = None  # hidden: existing repo test project to run post-edit
    evaluator_test_filter: str | None = None   # hidden: optional dotnet test filter for that project
    completion_test_project: str | None = None  # hidden: acceptance check, never the harm oracle
    completion_test_filter: str | None = None
    required_task_files: tuple[str, ...] = ()
    required_task_symbols: tuple[str, ...] = ()
    required_task_checks: tuple[str, ...] = ()
    build_solution: str = "TemplateBlueprint.sln"  # hidden: solution passed to dotnet build/test tools
    required_language_tier: str | None = None  # hidden: "risk_only" | "partial" | "full" preflight floor
    requires_measured_benefit: bool = False  # hidden: fail preflight unless RCA measured this patch
    requires_natural_safe_route: bool = False  # hidden: reference must re-assess to proceed without proof
    requires_graph_refinement_route: bool = False  # hidden: reference must prove Gate-9 graph evidence
    assay_p_success: float = 0.75  # hidden: task-specific benefit evidence used by the real advisory
    assay_immediate_benefit: float = 0.5
    assay_review_cost: float = 0.1
    language: str = "csharp"  # hidden: selects the build/test backend (csharp | javascript | typescript)
    harness_id: str = "dotnet"  # hidden: fixed backend profile family (dotnet | node)
    specimen: str = "csharp"  # hidden: specimen package under specimens/<name>/corpus
    repo_identity_files: tuple[str, ...] = ("TemplateBlueprint.sln",)  # hidden: source repo markers
    build_profile: str = "default"  # hidden: fixed profile name, never a shell command
    test_profile: str = "default"  # hidden: fixed profile name, never a shell command
    test_selector: str | None = None  # hidden: optional profile-specific selector
    build_selector: str | None = None  # hidden: optional profile-specific selector (e.g. "pkg:tsconfig")
    behavior_oracle: bool = False  # hidden: evaluator test defines task completion across valid layouts


# ---- one subject run (what the runner captures; scoring consumes it) --------------------------


@dataclass(frozen=True)
class ToolCallRecord:
    sequence: int                       # monotonic call order within the run
    name: str                           # tool name; mutating names are centralized in MUTATING_TOOLS
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubjectResult:
    """Everything the (future) real-agent runner captures for one arm of one task/seed.

    Build/test are stored as plain fields (not a DotNetBuildResult) so scoring is adapter-free."""

    task_id: str
    arm: str
    seed: int
    transcript: tuple[str, ...] = ()            # message texts, for the blinding scan
    tool_calls: tuple[ToolCallRecord, ...] = ()
    modified_files: tuple[str, ...] = ()        # tracked + untracked repo diffs, excluding harness dirs
    build_ran: bool = False
    build_passed: bool | None = None
    build_error_summary: str = ""
    test_ran: bool = False
    test_passed: bool | None = None
    completion_test_ran: bool = False
    completion_test_passed: bool | None = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None
    final_stop_reason: str | None = None
    limit_reason: str | None = None
    turn_count: int = 0
    served_models: tuple[str, ...] = ()
    protocol_file_read: bool = False
    post_edit_verify_ran: bool = False
    post_edit_verify_passed: bool | None = None
    post_edit_verify_assessment_id: str | None = None
    post_edit_verify_error: str | None = None
    applied_assessment_id: str | None = None
    measured_benefit: float = 0.0
    measured_benefit_deltas: dict[str, float] = field(default_factory=dict)
    human_approval_offered: bool = False
    human_approval_requested: bool = False
    human_approval_granted: bool = False
    human_approval_assessment_id: str | None = None
    human_approval_source: str | None = None
    post_approval_reassessment: bool = False
    human_assisted_write_applied: bool = False
    write_before_approval: bool = False
    write_before_reassessment: bool = False
    graph_refinement_status: str | None = None
    graph_refinement_assessment_id: str | None = None
    graph_refinement_selected: bool = False
    graph_refinement_language: str | None = None
    graph_refinement_witness: str | None = None
    graph_refinement_witness_version: str | None = None
    graph_refinement_engine_version: str | None = None
    graph_refinement_fact_kinds: tuple[str, ...] = ()
    graph_refinement_risk_probability_update_count: int = 0
    graph_refinement_risk_probability_updates: tuple[dict[str, Any], ...] = ()
    graph_refinement_origin_expected_loss: float | None = None
    graph_refinement_revised_expected_loss: float | None = None
    graph_refinement_origin_benefit: float | None = None
    graph_refinement_revised_benefit: float | None = None
    graph_refinement_origin_expected_utility: float | None = None
    graph_refinement_revised_expected_utility: float | None = None
    graph_refinement_origin_utility_sd: float | None = None
    graph_refinement_revised_utility_sd: float | None = None
    graph_refinement_origin_rau: float | None = None
    graph_refinement_revised_rau: float | None = None
    graph_refinement_candidate_verification_passed: bool = False
    graph_refinement_revision_risk_benefit_improved: bool = False
    graph_refinement_proof_path: str | None = None
    candidate_lineage_invalidated: bool = False
    calibration_assessment_id: str | None = None
    calibration_score_source: str | None = None
    calibration_join_valid: bool = False
    calibration_label_scope: str = "unresolved"
    predicted_decision: str | None = None
    predicted_expected_loss: float | None = None
    predicted_benefit: float | None = None
    predicted_expected_utility: float | None = None
    predicted_utility_sd: float | None = None
    predicted_rau: float | None = None
    predicted_effective_threshold: float | None = None
    predicted_benefit_source_type: str | None = None
    assessment_proof_class: str | None = None
    calibration_lanes: dict[str, Any] = field(default_factory=dict)
    prior_source: str | None = None
    prior_calibration_tags: tuple[str, ...] = ()


# ---- scored outcome ---------------------------------------------------------------------------

# adherence states
ADH_DID_NOT_CALL = "did_not_call"
ADH_HEEDED = "called_heeded"
ADH_IGNORED = "called_ignored"
ADH_NO_RESTRICTION = "called_no_restriction"  # advisory said proceed / gave no restriction
GUIDANCE_NOT_APPLICABLE = "not_applicable"
GUIDANCE_HEEDED_SAFE = "heeded_safe"
GUIDANCE_HEEDED_THEN_HARMED = "heeded_then_harmed"
GUIDANCE_IGNORED = "ignored"

# over-caution causes
OCC_GATE_BLOCKED = "gate_blocked"
OCC_ADVISORY_DISCOURAGED = "advisory_discouraged"
OCC_MODEL_DECLINED_UNPROMPTED = "model_declined_unprompted"
OCC_TIMEOUT = "timeout"


@dataclass(frozen=True)
class RunOutcome:
    task_id: str
    arm: str
    seed: int
    harm_label: str
    harm_materialized: bool
    task_completed: bool
    over_cautious: bool
    quality_failure: bool
    scope_drift: bool
    build_failed: bool
    test_failed: bool
    edit_cycle_count: int
    advisory_called: bool
    advisory_decision: str | None
    heeded_guidance: bool | None
    adherence_state: str
    blinding_leak: bool
    blinding_terms: tuple[str, ...]
    timed_out: bool
    completion_test_ran: bool = False
    completion_test_passed: bool | None = None
    post_edit_verify_ran: bool = False
    post_edit_verify_passed: bool | None = None
    post_edit_verify_assessment_id: str | None = None
    applied_assessment_id: str | None = None
    measured_benefit: float = 0.0
    measured_benefit_deltas: dict[str, float] = field(default_factory=dict)
    decision_cycle_completed: bool = False
    terminal_governance_outcome: str | None = None
    no_attempt: bool = False             # stopped without an edit attempt or restrictive gate
    error: str | None = None            # non-None => run failed (e.g. live client error); excluded from metrics
    limit_reason: str | None = None
    advisory_effective: bool = False
    served_models: tuple[str, ...] = ()
    over_caution_cause: str | None = None
    protocol_file_read: bool = False
    guidance_outcome: str = GUIDANCE_NOT_APPLICABLE
    human_approval_offered: bool = False
    human_approval_requested: bool = False
    human_approval_granted: bool = False
    human_approval_assessment_id: str | None = None
    human_approval_source: str | None = None
    post_approval_reassessment: bool = False
    human_assisted_write_applied: bool = False
    write_before_approval: bool = False
    write_before_reassessment: bool = False
    graph_refinement_status: str | None = None
    graph_refinement_assessment_id: str | None = None
    graph_refinement_selected: bool = False
    graph_refinement_language: str | None = None
    graph_refinement_witness: str | None = None
    graph_refinement_witness_version: str | None = None
    graph_refinement_engine_version: str | None = None
    graph_refinement_fact_kinds: tuple[str, ...] = ()
    graph_refinement_risk_probability_update_count: int = 0
    graph_refinement_risk_probability_updates: tuple[dict[str, Any], ...] = ()
    graph_refinement_origin_expected_loss: float | None = None
    graph_refinement_revised_expected_loss: float | None = None
    graph_refinement_origin_benefit: float | None = None
    graph_refinement_revised_benefit: float | None = None
    graph_refinement_origin_expected_utility: float | None = None
    graph_refinement_revised_expected_utility: float | None = None
    graph_refinement_origin_utility_sd: float | None = None
    graph_refinement_revised_utility_sd: float | None = None
    graph_refinement_origin_rau: float | None = None
    graph_refinement_revised_rau: float | None = None
    graph_refinement_candidate_verification_passed: bool = False
    graph_refinement_revision_risk_benefit_improved: bool = False
    graph_refinement_proof_path: str | None = None
    candidate_lineage_invalidated: bool = False
    language: str = "unknown"
    proof_class: str = "none"
    calibration_assessment_id: str | None = None
    calibration_score_source: str | None = None
    calibration_join_valid: bool = False
    calibration_label_scope: str = "unresolved"
    predicted_decision: str | None = None
    predicted_expected_loss: float | None = None
    predicted_benefit: float | None = None
    predicted_expected_utility: float | None = None
    predicted_utility_sd: float | None = None
    predicted_rau: float | None = None
    predicted_effective_threshold: float | None = None
    predicted_benefit_source_type: str | None = None
    calibration_lanes: dict[str, Any] = field(default_factory=dict)
    prior_source: str | None = None
    prior_calibration_tags: tuple[str, ...] = ()


# ---- aggregated metrics -----------------------------------------------------------------------


@dataclass(frozen=True)
class ArmMetrics:
    arm: str
    n_runs: int
    n_risky: int
    n_safe: int
    harm_rate: float                    # over risky runs
    over_caution_rate: float            # over safe runs
    quality_failure_rate: float         # over attempted runs
    task_completion_rate: float         # over all runs
    mean_edit_cycles: float
    adherence_rate: float | None        # advisory_called / n_runs
    heeded_rate: float | None           # heeded / advisory_called
    effective_adherence_rate: float | None = None  # successful advisory / n_runs
    error_run_count: int = 0            # runs excluded due to SubjectResult.error (e.g. live client failure)
    blinding_leak_count: int = 0        # runs excluded due to transcript/tool visibility leaks
    no_attempt_count: int = 0            # runs excluded because the subject never made a scorable attempt
    scope_drift_rate: float = 0.0       # over all non-error, non-leaked runs
    completion_test_run_count: int = 0
    completion_test_pass_count: int = 0
    completion_test_pass_rate: float | None = None
    decision_cycle_completion_count: int = 0
    decision_cycle_completion_rate: float | None = None
    autonomous_completion_count: int = 0
    autonomous_completion_rate: float | None = None
    human_assisted_completion_count: int = 0
    human_assisted_completion_rate: float | None = None
    safe_escalation_count: int = 0
    safe_escalation_rate: float | None = None
    approval_offered_count: int = 0
    approval_requested_count: int = 0
    approval_granted_count: int = 0
    approval_request_adherence_rate: float | None = None
    approval_grant_rate: float | None = None
    post_approval_reassessment_count: int = 0
    post_approval_reassessment_rate: float | None = None
    write_before_approval_count: int = 0
    write_before_approval_rate: float | None = None
    write_before_reassessment_count: int = 0
    write_before_reassessment_rate: float | None = None
    graph_refined_autonomous_completion_count: int = 0
    graph_refined_autonomous_completion_rate: float | None = None
    graph_only_autonomous_completion_count: int = 0
    graph_only_autonomous_completion_rate: float | None = None
    graph_plus_host_verified_completion_count: int = 0
    graph_plus_host_verified_completion_rate: float | None = None


@dataclass(frozen=True)
class ABMetrics:
    control: ArmMetrics
    treatment: ArmMetrics
    harm_avoided_rate: float            # control.harm_rate - treatment.harm_rate
    over_caution_delta: float           # treatment.over_caution_rate - control.over_caution_rate
    net_benefit: float                  # harm_avoided_rate - over_caution_delta
    n_pairs_risky: int
    n_pairs_safe: int
    # statistical summary (directional in a pilot; see README non-claims)
    cohens_d_paired: float | None
    wilcoxon_w: float | None
    wilcoxon_p: float | None
    harm_diff_ci95: tuple[float, float] | None

    @property
    def harm_overcaution_balance(self) -> float:
        """Legacy unweighted balance; not a total-benefit or decision-curve measure."""
        return self.net_benefit


# ---- multi-arm ASSAY metrics ------------------------------------------------------------------


@dataclass(frozen=True)
class PairwiseComparison:
    """One intervention-vs-baseline comparison, matched per (task_id, seed)."""

    intervention_arm: str
    baseline_arm: str
    n_pairs_risky: int
    n_pairs_safe: int
    harm_avoided_rate: float            # mean over risky pairs of (baseline_harm - intervention_harm)
    risky_completion_gain: float        # mean over risky pairs of (intervention_done - baseline_done)
    over_caution_delta: float           # mean over safe pairs of (intervention_oc - baseline_oc)
    net_benefit: float                  # harm_avoided_rate - over_caution_delta
    cohens_d_paired: float | None
    wilcoxon_w: float | None
    wilcoxon_p: float | None
    harm_diff_ci95: tuple[float, float] | None

    autonomous_completion_gain: float = 0.0
    human_assisted_completion_gain: float = 0.0
    graph_only_autonomous_completion_gain: float = 0.0
    graph_plus_host_verified_completion_gain: float = 0.0
    harm_avoided_count: int = 0
    completion_gain_count: int = 0
    over_caution_count: int = 0
    n_independent_risky_tasks: int = 0

    @property
    def harm_overcaution_balance(self) -> float:
        """Legacy unweighted balance; retained as ``net_benefit`` for artifact compatibility."""
        return self.net_benefit

    @property
    def graph_refined_post_edit_verified_completion_gain(self) -> float:
        return (
            self.graph_only_autonomous_completion_gain
            + self.graph_plus_host_verified_completion_gain
        )


@dataclass(frozen=True)
class AssayInterpretation:
    """The pre-registered verdict + the boolean gate trace that produced it."""

    verdict: str
    task_has_headroom: bool
    assay_detects_realistic: bool
    pebra_has_efficacy: bool
    pebra_exceeds_blast: bool
    graph_repair_exceeds_pebra: bool = False


@dataclass(frozen=True)
class AssayMetrics:
    # arm_metrics is a dict -> exclude it from the frozen-implied __hash__ (hashing a dict raises
    # TypeError). It stays required and is still compared by value in __eq__.
    arm_metrics: dict[str, ArmMetrics] = field(hash=False)
    pairwise: tuple[PairwiseComparison, ...] = field(hash=False)
    interpretation: AssayInterpretation = field(hash=False)
    n_arms: int = field(hash=False)
