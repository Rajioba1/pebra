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


# --- Port return types (Architecture §3 / §5 contracts). Phase 0 carries the subset used. ---


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
    """What EvidenceProvider returns (Phase 0): the scored inputs the engine needs.

    In Phase 0 these are read from the request's evidence block (elicited/configured/projected); later
    phases enrich them from radon/bandit/architecture map. The engine never sees the provider.
    """

    events: list[dict[str, Any]]
    p_success: float
    immediate_benefit: float
    review_cost: float
    criticality_stage: str
    criticality_value: float
    edit_confidence_factors: dict[str, float]
    thresholds: dict[str, float] = field(default_factory=dict)
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
    p_success_variance: float = 0.0
    review_cost_variance: float = 0.0
    variance_breakdown: dict[str, float] | None = None  # explicit variance (AD-5 precedence 1)
    benefit_delta_evidence: BenefitDeltaEvidence = field(default_factory=BenefitDeltaEvidence)
    symbol_diff_evidence: SymbolDiffEvidence = field(default_factory=SymbolDiffEvidence)
    blast_evidence: BlastEvidence = field(default_factory=BlastEvidence)
    architecture_evidence: ArchitectureEvidence = field(default_factory=ArchitectureEvidence)
    active_snapshot: Any | None = None  # no learning in Phase 0 (cold start)
    sanction: Any | None = None  # pre-fetched sanction (engine never calls a port)


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
    explanation: list[str] = field(default_factory=list)
    model_guidance_packet: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    decision_reason: str = ""
    assessed_commit: str | None = None  # repo HEAD at assess time; verify checks evidence freshness
