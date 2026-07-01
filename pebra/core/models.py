"""Core data models — the IR seam (Architecture §3, §7).

``AssessmentRequest`` is the one canonical request (AD-8). ``AssessmentInput`` is the normalized IR a
controller builds from ports and hands to the pure engine. ``AssessmentResult`` is what the engine
returns for the backend to render. The engine reads only ``AssessmentInput`` and returns only
``AssessmentResult``: anything needing git/sqlite/subprocess arrives *inside* ``AssessmentInput``.

Pure: stdlib dataclasses/typing only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pebra.core.constants import ActionStatus, Decision, GraphFreshness, RiskMode

SCHEMA_VERSION = "0.1"


@dataclass
class CandidateAction:
    """One candidate action under assessment (§3.1)."""

    id: str
    label: str
    action_type: str  # edit | information | ...
    proposed_patch: str | None = None
    affected_symbols: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    is_dependency_change: bool = False
    is_schema_change: bool = False
    is_migration: bool = False


@dataclass
class AssessmentRequest:
    """The one canonical request object (AD-8)."""

    task: str
    candidate_actions: list[CandidateAction] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def single_action(
        cls,
        task: str,
        action_id: str,
        label: str,
        action_type: str = "edit",
        **action_kwargs: Any,
    ) -> "AssessmentRequest":
        """AD-8 short form: build the same canonical request from a single action."""
        action = CandidateAction(
            id=action_id, label=label, action_type=action_type, **action_kwargs
        )
        return cls(task=task, candidate_actions=[action])


# --- Port return types (Architecture §3 / §5 contracts). ---


@dataclass(frozen=True)
class SymbolDiffEvidence:
    """Layer-1 symbol/scope evidence (canonical assessment evidence, not just a high-risk filter)."""

    parsed_patch_available: bool = False
    changed_symbols: list[str] = field(default_factory=list)
    max_change_kind: str = "UNKNOWN"
    visibility: str = "unknown"
    consequential_symbol_changed: bool = False
    consequence_reason: list[str] = field(default_factory=list)
    symbol_fan_in_percentile: float = 0.0
    transitive_reaches_consequence_symbol: bool = False
    directive_comment_changed: bool = False
    fallback_reason: str | None = None
    # File-level operation axis (separate from max_change_kind, which is symbol-semantic). Detected
    # from patch headers; "NONE" for ordinary modify patches. file_operation_paths holds the old-side
    # path(s) of the affected file(s) for the fan-in roll-up / event injection.
    file_operation_kind: str = "NONE"  # FileOperationKind value
    file_operation_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FanInEvidence:
    """Language-agnostic per-symbol fan-in, resolved by location through codegraph's call graph.

    The robust cross-language contract (M5c.5): the changed symbol is identified by (file, old-side
    line range) — never by a guessed name — codegraph maps that location to a node, and the fan-in is
    the reverse-edge count over call-like edges (calls/references/instantiates; NOT imports, which is
    file/module-level). PEBRA owns the percentile math (core.score_math.fractional_rank); codegraph
    owns identity + resolution. ``resolution_method`` records how the symbol was found so a name
    fallback or an unresolved/stale read is auditable rather than silently scored as zero fan-in.

    graph_freshness mirrors codegraph's own signal: 'fresh' (status clean) | 'stale' (pendingChanges
    or reindexRecommended — fan-in is NOT trusted, percentile stays 0.0) | 'unknown' (engine/DB
    absent). Provider/index versions are carried for provenance and calibration scope.
    """

    symbol_fan_in_percentile: float = 0.0
    symbol_caller_count: int = 0
    resolution_method: str = "unresolved"  # 'location' | 'name_fallback' | 'name_fallback_ambiguous' | 'unresolved'
    node_ids_resolved: tuple[str, ...] = ()
    provider_version: str | None = None
    index_version: str | None = None
    graph_freshness: str = "unknown"  # 'fresh' | 'stale' | 'unknown'
    fallback_reason: str | None = None
    # Graph-wide context for the resolved owner nodes. These are not additional provider verdicts;
    # they are raw CodeGraph facts used by MODIFY risk modeling and surfaced for audit.
    owner_kinds: tuple[str, ...] = ()
    max_owner_span_lines: int = 0
    resolved_symbol_count: int = 0
    incoming_edge_counts: dict[str, int] = field(default_factory=dict)
    outgoing_edge_counts: dict[str, int] = field(default_factory=dict)
    modify_impact_count: int = 0
    modify_impact_percentile: float = 0.0
    modify_impact_edge_counts: dict[str, int] = field(default_factory=dict)
    container_hierarchy_kinds: tuple[str, ...] = ()
    graph_file_size_bytes: int = 0
    graph_file_node_count: int = 0
    graph_file_error_count: int = 0
    contract_surface_kind: str = "unknown"
    is_exported_contract: bool = False
    is_abstract_or_interface_contract: bool = False
    has_signature_metadata: bool = False


@dataclass(frozen=True)
class FileFanInRollup:
    """Aggregate call-graph fan-in across ALL callable symbols in a file — for whole-file destructive
    ops (DELETE) where a single changed symbol understates the impact. ``distinct_caller_count`` is the
    UNION of distinct callers across every callable in the file (what breaks when the file is deleted);
    ``max_caller_count`` is the worst single symbol. The percentile is ranked against the same repo-wide
    distribution used for per-symbol fan-in (score_math.fractional_rank). ``resolution_method`` mirrors
    FanInEvidence: 'file_location' when a fresh graph answered, 'unresolved' otherwise (no trust)."""

    max_caller_count: int = 0
    distinct_caller_count: int = 0
    symbol_count: int = 0
    file_symbol_fanin_rollup_percentile: float = 0.0
    resolution_method: str = "unresolved"  # 'file_location' | 'unresolved'
    graph_freshness: str = "unknown"
    fallback_reason: str | None = None


@dataclass(frozen=True)
class BlastEvidence:
    direct_count: int = 0
    transitive_count: int = 0
    depth_buckets: dict[int, int] = field(default_factory=dict)
    edge_confidence_mean: float = 0.0
    edge_confidence_min: float = 0.0  # AD-12: lowest-confidence edge in the reach
    low_confidence_edge_count: int = 0  # AD-12: edges below the confidence floor
    entrypoint_signal: bool = False
    import_cycle_detected: bool = False
    # 3c — graph-incompleteness as evidence. Counts scoped to the changed files unless noted.
    missing_file_count: int = 0  # expected_files absent from the repo
    parse_error_count: int = 0  # expected_files present but unparseable
    unresolved_import_count: int = 0  # internal imports that failed to resolve (real failures)
    dynamic_import_count: int = 0  # importlib/__import__ in the changed files
    wildcard_import_count: int = 0  # `from x import *` in the changed files
    external_import_count: int = 0  # stdlib/third-party imports — tracked, NOT penalized
    graph_uncertainty_score: float = 0.0  # [0, cap] bounded penalty applied to evidence_quality
    graph_uncertainty_reason: str = ""  # human-facing explanation of the incompleteness
    # 3d provenance — WHAT couldn't be resolved (bounded, "file: name"), for model guidance.
    unresolved_imports: tuple[str, ...] = ()
    dynamic_imports: tuple[str, ...] = ()
    wildcard_imports: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    parse_error_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArchitectureEvidence:
    """Derived codebase map (AD-22) — structural signals, not decision memory (Architecture §5).

    graph_freshness: fresh | rebuilt | stale | unknown. 'rebuilt' = was stale, the adapter
    successfully repaired the map (trustworthy). 'stale' = unresolved (rebuild failed) — the
    evidence-validity gate routes that to inspect_first.
    """

    graph_commit: str | None = None
    graph_freshness: GraphFreshness = GraphFreshness.UNKNOWN
    matched_anchors: list[str] = field(default_factory=list)
    matched_domains: list[str] = field(default_factory=list)
    architecture_anchor_score: float = 0.0
    god_node_score: float = 0.0  # 3f: repo-relative fan-in percentile (0 below the in-degree floor)
    bridge_centrality: float = 0.0
    domain_entrypoint: bool = False
    fan_out: int = 0  # 3f: outgoing import count of the edited file(s) — coupling breadth
    cycle_participation: bool = False  # 3f: an edited file sits in an import cycle (SCC > 1)
    domain_criticality_hint: str | None = None
    source_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenefitDeltaEvidence:
    scope: str = ""
    source_type: str = "projected"  # projected | derived | measured
    deltas: dict[str, float] = field(default_factory=dict)
    future_change_exposure: float = 0.0


@dataclass(frozen=True)
class ActualDiffSummary:
    """What ChangeVerifier returns about the *actual* post-edit diff (Architecture §9)."""

    current_head: str | None = None
    changed_files: list[str] = field(default_factory=list)
    dependency_changed: bool = False
    schema_changed: bool = False
    migration_changed: bool = False
    actual_max_change_kind: str = "UNKNOWN"
    actual_changed_symbols: list[str] = field(default_factory=list)
    # A1 (M5c.5): post-edit consequence verdict from the reclassifier, now fed by REAL per-symbol fan-in
    # (graph engine) — symmetric with the assess path. Lets the guardrail escalate a high-fan-in
    # consequential change the pre-edit assessment didn't flag.
    actual_consequential_symbol_changed: bool = False
    actual_consequence_reason: list[str] = field(default_factory=list)
    measured_benefit_deltas: dict[str, float] = field(default_factory=dict)
    # True iff ≥1 changed file was a Python file we attempted to reclassify. Distinguishes
    # "couldn't parse code we changed" (escalate) from "no code symbols at all" (don't).
    reclassification_attempted: bool = False


@dataclass(frozen=True)
class ContractSurfaceFindings:
    """What ContractSurfaceProvider returns (Architecture §9): detected public-surface changes."""

    changes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceBundle:
    """What EvidenceProvider returns: the scored inputs the engine needs.

    These may originate from request evidence, config, static analysis, architecture maps, graph
    adapters, or other outer-layer providers. The engine never sees the provider.
    """

    events: list[dict[str, Any]]
    p_success: float
    immediate_benefit: float
    review_cost: float
    criticality_stage: str
    criticality_value: float
    edit_confidence_factors: dict[str, float]
    thresholds: dict[str, float] = field(default_factory=dict)
    policy_violations: list[str] = field(default_factory=list)
    variance_breakdown: dict[str, float] | None = None
    p_success_variance: float = 0.0
    review_cost_variance: float = 0.0
    benefit_delta_evidence: "BenefitDeltaEvidence" = field(default_factory=lambda: BenefitDeltaEvidence())
    architecture_evidence: "ArchitectureEvidence" = field(default_factory=lambda: ArchitectureEvidence())


@dataclass
class AssessmentInput:
    """The IR the pure engine consumes. Built by a controller from validated request + ports."""

    request: AssessmentRequest
    action: CandidateAction
    events: list[dict[str, Any]]
    p_success: float
    immediate_benefit: float
    review_cost: float
    criticality_stage: str
    criticality_value: float
    edit_confidence_factors: dict[str, float]
    thresholds: dict[str, float]
    repo_id: str
    repo_root: str
    policy_violations: list[str] = field(default_factory=list)
    p_success_variance: float = 0.0
    review_cost_variance: float = 0.0
    variance_breakdown: dict[str, float] | None = None  # explicit variance (AD-5 precedence 1)
    benefit_delta_evidence: BenefitDeltaEvidence = field(default_factory=BenefitDeltaEvidence)
    symbol_diff_evidence: SymbolDiffEvidence = field(default_factory=SymbolDiffEvidence)
    # M5c.5: language-agnostic per-symbol fan-in (codegraph-backed). None until the adapter is wired
    # / codegraph is present; carried for provenance even when resolution_method='unresolved'.
    fanin_evidence: "FanInEvidence | None" = None
    # File-level fan-in roll-up for whole-file destructive ops; None for ordinary edits / no graph.
    file_fanin_rollup: "FileFanInRollup | None" = None
    blast_evidence: BlastEvidence = field(default_factory=BlastEvidence)
    architecture_evidence: ArchitectureEvidence = field(default_factory=ArchitectureEvidence)
    active_snapshot: Any | None = None  # read-only learned snapshot bundle; None for cold start
    sanction: Any | None = None  # pre-fetched sanction (engine never calls a port)
    # Structural feature payload attached pre-scoring for CAPTURE only.
    # assessment_builder/decision_engine MUST ignore it (no score/gate change); persisted with the
    # prediction manifest and consumed by M5 apply_snapshot. None until enrichment is wired.
    structural_features: dict[str, Any] | None = None
    # M5b: provenance of learned-override facts applied by apply_snapshot (which fact won each target,
    # prior vs new value). Set on the adjusted copy; builder/engine IGNORE it. None when nothing applied.
    applied_snapshot_provenance: dict[str, Any] | None = None
    # Benefit-continuous learned override for the final projected/measured benefit target. None unless an
    # active ``measured_benefit`` fact applied; assessment_builder owns the actual score replacement.
    benefit_override: float | None = None


@dataclass
class AssessmentResult:
    """What the engine returns; the backend renders it (card / JSON / MCP / dashboard / SQLite)."""

    recommended_decision: Decision
    requires_confirmation: bool
    action_status: ActionStatus
    risk_mode: RiskMode
    scores: dict[str, Any]
    repo_id: str
    repo_root: str
    gates_fired: list[dict[str, Any]] = field(default_factory=list)
    high_risk_triggers: list[dict[str, Any]] = field(default_factory=list)
    symbol_scope_evidence: dict[str, Any] = field(default_factory=dict)
    graph_evidence: dict[str, Any] = field(default_factory=dict)  # 3c/3d blast graph incompleteness
    fanin_validity: dict[str, Any] = field(default_factory=dict)  # M5c.5 Gate 13 evidence-validity
    explanation: list[str] = field(default_factory=list)
    model_guidance_packet: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    decision_reason: str = ""
    assessed_commit: str | None = None  # repo HEAD at assess time; verify checks evidence freshness
