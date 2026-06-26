"""Composition root (Architecture §2) — the single place concrete adapters are wired for the assess
and verify use cases, shared by every surface (CLI + MCP) so the two never drift.

This module may import adapters and app types; ``core``, ``ports`` and ``adapters`` must never import
it (enforced in .importlinter). A surface resolves the repo/store, asks here for the adapter bundle,
hands it to the app controller, then serialises the outcome via the payload helpers below.

It is import-cheap: every adapter pulled in here is stdlib-backed or lazy (CompositeEvidenceProvider
defers yaml/radon), so the dep-light CLI and the worked-example golden never pull a heavy library.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pebra.adapters import git_adapter
from pebra.adapters.ast_diff_adapter import AstDiffAdapter
from pebra.adapters.ast_import_graph import AstImportGraphAdapter
from pebra.adapters.composite_evidence import CompositeEvidenceProvider
from pebra.adapters.contract_surface import ContractSurfaceScanner
from pebra.adapters.git_change_verifier import GitChangeVerifier
from pebra.adapters.import_graph_cache import GraphProvider
from pebra.adapters.repository_registry import RepositoryRegistry
from pebra.adapters.sanction_store import SanctionStore
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


def build_assess_ports(request: AssessmentRequest, ctx: RepoContext) -> dict[str, Any]:
    """The adapter bundle ``assess_controller.assess`` needs (keyword args minus thresholds/start_path).
    One GraphProvider is shared by the architecture + blast adapters (build-once memo, 5c)."""
    graph_provider = GraphProvider()
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
        # Phase-4 reframe: PEBRA-owned structural feature capture (no external codeindex/sem). Shared
        # by CLI + MCP so both persist the same feature payload with predictions.
        "structural_feature_provider": StructuralFeatureAdapter(),
    }


def build_verify_ports() -> dict[str, Any]:
    """The (stateless) adapter bundle ``verify_controller.verify`` needs."""
    return {"change_verifier": GitChangeVerifier(), "contract_surface": ContractSurfaceScanner()}


def build_sanction_port(ctx: RepoContext) -> SanctionPort:
    """The sanction port for accept-risk, wired over the open store."""
    return SanctionStore(ctx.store)


# --- canonical surface payloads (shared by CLI --json and MCP tool results) ----


def assess_payload(outcome: AssessmentOutcome) -> dict[str, Any]:
    """The canonical assess result (the recommended action). Identical bytes for `assess --json` and
    the pebra_assess MCP tool."""
    r = outcome.recommended_result
    return {
        "recommended_decision": r.recommended_decision.value,
        "requires_confirmation": r.requires_confirmation,
        "risk_mode": r.risk_mode.value,
        "action_status": r.action_status.value,
        "repo_id": outcome.repo_id,
        "assessment_id": outcome.assessment_id,
        "scores": r.scores,
        "why": outcome.recommended_explanation.why,
        "gates_fired": r.gates_fired,
        "high_risk_triggers": r.high_risk_triggers,
        "model_guidance_packet": r.model_guidance_packet,
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
    return payload
