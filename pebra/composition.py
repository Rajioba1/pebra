"""Composition root (Architecture §2) — the single place concrete adapters are wired for the assess
and verify use cases, shared by every surface (CLI + MCP) so the two never drift.

This module may import adapters and app types; ``core``, ``ports`` and ``adapters`` must never import
it (enforced in .importlinter). A surface resolves the repo/store, asks here for the adapter bundle,
hands it to the app controller, then serialises the outcome via the payload helpers below.

It is import-cheap: every adapter pulled in here is stdlib-backed or lazy (CompositeEvidenceProvider
defers yaml), so the dep-light CLI and the worked-example golden never pull a heavy library.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pebra.adapters import git_adapter
from pebra.adapters.ast_diff_adapter import AstDiffAdapter
from pebra.adapters.ast_import_graph import AstImportGraphAdapter
from pebra.adapters.composite_evidence import CompositeEvidenceProvider
from pebra.adapters.contract_surface import ContractSurfaceScanner
from pebra.adapters.git_change_verifier import GitChangeVerifier
from pebra.adapters.candidate_binding import CandidateBindingAdapter
from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.adapters.codegraph_materialized_diff import CodeGraphMaterializedDiffAdapter
from pebra.adapters.import_graph_cache import GraphProvider
from pebra.adapters.rca_adapter import RustCodeAnalysisAdapter
from pebra.adapters.repository_registry import RepositoryRegistry
from pebra.adapters.sanction_store import SanctionStore
from pebra.adapters.snapshot_read_store import SnapshotReadStore
from pebra.adapters.store.db import SqliteStore
from pebra.adapters.structural_feature_adapter import StructuralFeatureAdapter
from pebra.app.assess_controller import AssessmentOutcome, ScoredAction
from pebra.app.verify_controller import VerifyOutcome
from pebra.core.models import AssessmentRequest
from pebra.ports.sanction_port import SanctionPort


@dataclass
class RepoContext:
    """The resolved repo + an open store, shared by a surface call. The surface owns the store
    lifecycle (``ctx.store.close()`` in a finally)."""

    registry: RepositoryRegistry
    repo: Any  # RepoMetadata (repo_id, repo_root)
    store: SqliteStore
    db_path: str


def resolve_repo_and_db(start_path: str, db_path: str | None = None) -> RepoContext:
    """Resolve the repo from ``start_path`` and open the store at ``db_path`` (default
    ``<repo_root>/.pebra/pebra.db``). Identical resolution for every surface."""
    registry = RepositoryRegistry()
    repo = registry.resolve(start_path)
    resolved = db_path or str(Path(repo.repo_root) / ".pebra" / "pebra.db")
    return RepoContext(registry=registry, repo=repo, store=SqliteStore(resolved), db_path=resolved)


def graph_node_counts(repo_root: str) -> dict[str, int]:
    """Repo-wide CodeGraph node counts via the codegraph adapter (used by `pebra graph-stats` and the
    A/B graph preflight for an independent graph-validity check). Zeros when the graph is absent."""
    return CodeGraphAdapter().node_counts(repo_root)


def probe_language_capabilities(repo_root: str) -> list[dict[str, Any]]:
    """MEASURED per-language capability for `pebra capabilities`: one serializable row per indexed
    language with its support tier + coverage. Empty when the graph is absent. Sorted by node_count
    desc so the best-covered languages lead."""
    from pebra.core.language_capability import classify_tier  # noqa: PLC0415

    caps = CodeGraphAdapter().probe_capabilities(repo_root)
    rows = [
        {
            "language": cap.language,
            "tier": classify_tier(cap),
            "node_count": cap.node_count,
            "signature_coverage_ratio": round(cap.signature_coverage_ratio, 3),
            "visibility_coverage_ratio": round(cap.visibility_coverage_ratio, 3),
            "edge_kinds": sorted(cap.edge_kinds),
        }
        for cap in caps.values()
    ]
    return sorted(rows, key=lambda r: (-r["node_count"], r["language"]))


def dependent_files(repo_root: str, target: str) -> list[str]:
    """Repo-relative files that depend on ``target`` (the file-level blast radius), via the codegraph
    adapter. Empty when the graph is absent. Used by `pebra dependents`."""
    return CodeGraphAdapter().dependent_files(target, repo_root)


def dependent_files_result(repo_root: str, target: str) -> dict[str, Any]:
    """Structured file-level blast radius with graph availability/fallback metadata."""
    return CodeGraphAdapter().dependent_files_result(target, repo_root)


def build_assess_ports(request: AssessmentRequest, ctx: RepoContext) -> dict[str, Any]:
    """The adapter bundle ``assess_controller.assess`` needs (keyword args minus thresholds/start_path).
    One GraphProvider is shared by the architecture + blast adapters (build-once memo, 5c)."""
    graph_provider = GraphProvider()
    # One CodeGraphAdapter serves both the per-symbol fan-in and the whole-file roll-up (it satisfies
    # both ports structurally) — sharing the instance shares its distribution cache (one DB scan).
    codegraph = CodeGraphAdapter()
    return {
        "evidence_provider": CompositeEvidenceProvider(graph_provider=graph_provider),
        "symbol_diff_provider": AstDiffAdapter(request.evidence.get("symbol_diff")),
        "blast_provider": AstImportGraphAdapter(
            request.evidence.get("blast"), graph_provider=graph_provider
        ),
        "sanction_port": SanctionStore(ctx.store),
        "repository_registry": ctx.registry,
        "store": ctx.store,
        "assessed_commit": git_adapter.head_commit(ctx.repo.repo_root),
        "worktree_dirty": git_adapter.worktree_dirty(ctx.repo.repo_root),
        # Phase-4 reframe: PEBRA-owned structural feature capture (no external codeindex/sem). Shared
        # by CLI + MCP so both persist the same feature payload with predictions.
        "structural_feature_provider": StructuralFeatureAdapter(),
        # M5c: read-only active-snapshot provider (learned overrides applied pre-scoring). Read-only —
        # never writes learning. Cold-start (no active facts) -> identity, golden unchanged.
        "snapshot_read_port": SnapshotReadStore(ctx.store),
        # M5c.5: language-agnostic per-symbol fan-in via codegraph. Optional by default — when the
        # codegraph DB/CLI is absent it returns unresolved and the controller leaves scoring unchanged
        # (golden preserved). Set threshold ``require_graph`` true once codegraph is deployed to make
        # an unresolved/stale graph fail-clear through Gate 13 instead of silently treating absent
        # fan-in as low fan-in.
        "fanin_provider": codegraph,
        # Destructive-op (DELETE) whole-file fan-in roll-up — same adapter, fail-soft when absent.
        "file_fanin_provider": codegraph,
        # Multi-language: same adapter probes DECLARED∩MEASURED per-language capability so the
        # controller can attach the resolved edit's language capability (honest per-language reach).
        "language_capability_provider": codegraph,
        # Semantic tier: wired but DARK — the adapter is armed (enabled=True) yet dispatch only calls it
        # when the deployment flag below is on, the request threshold opts in, and the resolved language
        # measures `full`. Request thresholds alone cannot enable the live path.
        "materialized_diff_provider": CodeGraphMaterializedDiffAdapter(enabled=True),
        # Deployment dark gate: request thresholds alone cannot turn the expensive semantic tier on.
        "semantic_diff_enabled": os.environ.get("PEBRA_CODEGRAPH_SEMANTIC_DIFF") == "1",
        "candidate_binding_provider": CandidateBindingAdapter(),
    }


def build_verify_ports() -> dict[str, Any]:
    """The (stateless) adapter bundle ``verify_controller.verify`` needs.

    A1: the verifier gets the graph engine's per-symbol fan-in lookup so post-edit reclassification
    sees real fan-in (symmetric with assess). Absent codegraph -> the lookup returns {} -> verify keeps
    its pre-A1 behavior."""
    # One CodeGraphAdapter backs both the per-symbol fan-in lookup (Python rows) and the multi-language
    # structural reclassification of non-Python changed files (else they'd be silently skipped).
    codegraph = CodeGraphAdapter()
    return {
        "change_verifier": GitChangeVerifier(
            fanin_lookup=codegraph.percentiles_by_name,
            structural_symbols_fn=codegraph.structural_symbols,
            # Semantic reproduction (symmetry with assess), also DARK behind the same threshold: a
            # non-Python source file is re-diffed at the semantic tier so a semantic-tier approval is
            # reproducible at verify (else the 3d guardrail would over-escalate it once live).
            materialized_diff_fn=CodeGraphMaterializedDiffAdapter(enabled=True).diff,
            language_capability_fn=codegraph.capability_for,
            semantic_diff_enabled=os.environ.get("PEBRA_CODEGRAPH_SEMANTIC_DIFF") == "1",
            # Multi-language BENEFIT measurement (RCA), symmetric with the assess-path benefit provider.
            complexity_delta_fn=RustCodeAnalysisAdapter().measure_file_delta,
        ),
        "contract_surface": ContractSurfaceScanner(),
    }


def build_sanction_port(ctx: RepoContext) -> SanctionPort:
    """The sanction port for accept-risk, wired over the open store."""
    return SanctionStore(ctx.store)


# --- canonical surface payloads (shared by CLI --json and MCP tool results) ----


def _recommended_action_id(outcome: AssessmentOutcome) -> str | None:
    result = outcome.recommended_result
    for scored in outcome.scored_actions:
        if scored.result is result:
            return scored.action.id
    return None


def _next_action(outcome: AssessmentOutcome) -> dict[str, Any]:
    result = outcome.recommended_result
    decision = result.recommended_decision.value
    reason = result.decision_reason
    if decision == "ask_human":
        packet = result.model_guidance_packet or {}
        binding = packet.get("binding") or {}
        scores = result.scores or {}
        return {
            "type": "request_human_approval",
            "status": "pending",
            "assessment_id": outcome.assessment_id,
            "action_id": _recommended_action_id(outcome),
            "candidate_binding": binding.get("candidate"),
            "risk_benefit": {
                key: scores.get(key)
                for key in ("expected_loss", "benefit", "expected_utility", "rau")
            },
            "reason": reason,
            "required_controls": list(binding.get("required_controls") or []),
            # This packet is intentionally descriptive, not a ready-to-submit sanction. Approval must
            # cross a trusted host/operator boundary; request JSON is not approval evidence.
            "trusted_actor_required": True,
        }
    if decision == "revise_safer":
        return {"type": "resubmit_safer_candidate", "reason": reason}
    if decision == "inspect_first":
        return {"type": "inspect_then_reassess", "reason": reason}
    if decision == "test_first":
        return {"type": "run_checks_then_reassess", "reason": reason}
    if decision == "proceed":
        return {"type": "apply_exact_candidate_then_verify", "reason": reason}
    return {"type": "stop", "reason": reason}


def assess_payload(outcome: AssessmentOutcome) -> dict[str, Any]:
    """The canonical assess result (the recommended action). Identical bytes for `assess --json` and
    the pebra_assess MCP tool."""
    r = outcome.recommended_result
    repo_state = r.provenance.get("repo_state") or {
        "repo_head_sha": r.assessed_commit,
        "worktree_dirty": None,
        "assessed_repo_root": r.repo_root,
    }
    return {
        "recommended_decision": r.recommended_decision.value,
        "requires_confirmation": r.requires_confirmation,
        "risk_mode": r.risk_mode.value,
        "action_status": r.action_status.value,
        "repo_id": outcome.repo_id,
        "assessment_id": outcome.assessment_id,
        "scores": r.scores,
        "decision_reason": r.decision_reason,
        "next_action": _next_action(outcome),
        "why": outcome.recommended_explanation.why,
        "gates_fired": r.gates_fired,
        "high_risk_triggers": r.high_risk_triggers,
        "model_guidance_packet": r.model_guidance_packet,
        "applied_snapshot_provenance": r.provenance.get("applied_snapshot_provenance"),
        "repo_state": repo_state,
        "graph_provenance": _graph_provenance(r),
    }


def _graph_provenance(r: Any) -> dict[str, Any]:
    sse = r.scores.get("symbol_scope_evidence", {}) if isinstance(r.scores, dict) else {}
    symbol = dict(sse["symbol_fanin"]) if isinstance(sse.get("symbol_fanin"), dict) else None
    if symbol is not None:
        symbol.pop("provider_version", None)
        symbol.pop("index_version", None)
    rollup = sse.get("file_fanin_rollup")
    freshness_values = [
        item.get("graph_freshness")
        for item in (symbol, rollup)
        if isinstance(item, dict) and item.get("graph_freshness")
    ]
    stored = r.provenance.get("graph_provenance") or {}
    has_graph_evidence = symbol is not None or rollup is not None
    return {
        "engine": stored.get("engine") or ("CodeGraph" if has_graph_evidence else None),
        "graph_freshness": "fresh" if "fresh" in freshness_values else (
            freshness_values[0] if freshness_values else "unknown"
        ),
        "provider_version": stored.get("provider_version"),
        "index_version": stored.get("index_version"),
        "symbol_fanin": symbol,
        "file_fanin_rollup": rollup,
        "fanin_validity": r.fanin_validity,
        # Multi-language honesty must reach the actual JSON/MCP consumer (not just internal provenance):
        # which structural tier classified this diff, and the measured per-language capability.
        "structure_tier": stored.get("structure_tier") or sse.get("structure_tier"),
        "language_capability": stored.get("language_capability"),
    }


def _scored_action_payload(scored: ScoredAction) -> dict[str, Any]:
    r = scored.result
    return {
        "action_id": scored.action.id,
        "decision": r.recommended_decision.value,
        "requires_confirmation": r.requires_confirmation,
        "risk_mode": r.risk_mode.value,
        "action_status": r.action_status.value,
        "scores": r.scores,
        "why": scored.explanation.why,
        "gates_fired": r.gates_fired,
        "high_risk_triggers": r.high_risk_triggers,
    }


def compare_payload(outcome: AssessmentOutcome) -> dict[str, Any]:
    """The full multi-action comparison: every scored action plus the recommended one (pebra_compare)."""
    return {
        "recommended": assess_payload(outcome),
        "scored_actions": [_scored_action_payload(s) for s in outcome.scored_actions],
    }


def verify_payload(outcome: VerifyOutcome) -> dict[str, Any]:
    """The canonical verify result. Identical bytes for `verify --json` and the pebra_verify tool."""
    payload = asdict(outcome.result)
    payload["pre_commit_decision"] = outcome.result.pre_commit_decision.value
    payload["guardrails_id"] = outcome.guardrails_id
    # Post-edit RCA benefit — the CLI-boundary observable for the measured maintainability delta (kept
    # in sync with what's persisted to the store / shown on the dashboard, not a dashboard-only signal).
    payload["measured_benefit"] = outcome.measured_benefit
    payload["measured_benefit_deltas"] = dict(outcome.measured_benefit_deltas)
    return payload
