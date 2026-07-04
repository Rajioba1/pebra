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
ARM_BLAST_RADIUS = "blast_radius"        # CTXO-STYLE positive control on PEBRA's graph (NOT literal CTXO)
ARM_PEBRA = "pebra"                      # experimental treatment
ARM_ORACLE_POSITIVE = "oracle_positive"  # endpoint floor: correct fix pre-applied before the agent runs

# Pre-registered assay verdicts (checked in order; see metrics/assay_interpret.py).
VERDICT_NO_HEADROOM = "INVALID_NO_HEADROOM"
VERDICT_ASSAY_INSENSITIVE = "INVALID_ASSAY_INSENSITIVE"
VERDICT_PEBRA_INFERIOR = "PEBRA_INFERIOR"
VERDICT_PEBRA_PARTIAL = "PEBRA_EFFICACY_PARTIAL"
VERDICT_PEBRA_SUPERIOR = "PEBRA_SUPERIOR"


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
    build_solution: str = "TemplateBlueprint.sln"  # hidden: solution passed to dotnet build/test tools


# ---- one subject run (what the runner captures; scoring consumes it) --------------------------


@dataclass(frozen=True)
class ToolCallRecord:
    sequence: int                       # monotonic call order within the run
    name: str                           # "read_file" | "write_file" | "run_build" | "run_tests" | "advisory_check"
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
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None
    final_stop_reason: str | None = None
    turn_count: int = 0
    served_models: tuple[str, ...] = ()


# ---- scored outcome ---------------------------------------------------------------------------

# adherence states
ADH_DID_NOT_CALL = "did_not_call"
ADH_HEEDED = "called_heeded"
ADH_IGNORED = "called_ignored"
ADH_NO_RESTRICTION = "called_no_restriction"  # advisory said proceed / gave no restriction


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
    error: str | None = None            # non-None => run failed (e.g. live client error); excluded from metrics
    advisory_effective: bool = False
    served_models: tuple[str, ...] = ()


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
    scope_drift_rate: float = 0.0       # over all non-error, non-leaked runs


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


# ---- multi-arm ASSAY metrics ------------------------------------------------------------------


@dataclass(frozen=True)
class PairwiseComparison:
    """One intervention-vs-baseline comparison, matched per (task_id, seed)."""

    intervention_arm: str
    baseline_arm: str
    n_pairs_risky: int
    n_pairs_safe: int
    harm_avoided_rate: float            # mean over risky pairs of (baseline_harm - intervention_harm)
    over_caution_delta: float           # mean over safe pairs of (intervention_oc - baseline_oc)
    net_benefit: float                  # harm_avoided_rate - over_caution_delta
    cohens_d_paired: float | None
    wilcoxon_w: float | None
    wilcoxon_p: float | None
    harm_diff_ci95: tuple[float, float] | None


@dataclass(frozen=True)
class AssayInterpretation:
    """The pre-registered verdict + the boolean gate trace that produced it."""

    verdict: str
    task_has_headroom: bool
    assay_detects_realistic: bool
    pebra_has_efficacy: bool
    pebra_exceeds_blast: bool


@dataclass(frozen=True)
class AssayMetrics:
    # arm_metrics is a dict -> exclude it from the frozen-implied __hash__ (hashing a dict raises
    # TypeError). It stays required and is still compared by value in __eq__.
    arm_metrics: dict[str, ArmMetrics] = field(hash=False)
    pairwise: tuple[PairwiseComparison, ...] = field(hash=False)
    interpretation: AssayInterpretation = field(hash=False)
    n_arms: int = field(hash=False)
