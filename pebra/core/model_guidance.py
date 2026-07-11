"""model_guidance (Architecture §8, AD-23) — pure rendering of the pre-edit autonomy envelope.

This is NOT a second reasoning system and is not authored by an LLM. It is a deterministic rendering
of the approved action envelope, the selected decision, and the explanation/gate outputs. Binding
fields are enforced later by ``pebra_verify``; advisory fields steer the model without creating new
hard gates. Everything here must be reconstructable from canonical JSON.
"""

from __future__ import annotations

from typing import Any

from pebra.core import high_risk_controls
from pebra.core.constants import UNCERTAIN_STRUCTURE_TIERS
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult, CandidateAction

# Default risky-scope items: touching any of these invalidates the prior risk score (reassessment).
# Each carries a `signal` so pebra_verify can map it to an actual-diff signal (Architecture §9).
_DEFAULT_RISKY_SCOPE = [
    {"change": "public API changes", "action": "requires_reassessment", "signal": "contract_change"},
    {"change": "dependency upgrades", "action": "requires_reassessment", "signal": "dependency_changed"},
    {"change": "schema changes", "action": "requires_reassessment", "signal": "schema_changed"},
]


def _safe_scope_files(action: CandidateAction) -> list[str]:
    files: list[str] = []
    files.extend(action.affected_symbols)
    for f in action.expected_files:
        if f not in files:
            files.append(f)
    return files


def _safer_route(result: AssessmentResult, action: CandidateAction) -> dict[str, Any] | None:
    if result.recommended_decision.value != "revise_safer":
        return None
    sse = result.symbol_scope_evidence or {}
    constraints = [
        "Do not apply the current patch as-is; propose a narrower candidate and resubmit assessment.",
    ]
    if str(sse.get("file_operation_kind", "NONE")) in {"DELETE", "RENAME", "MOVE"}:
        constraints.append("Avoid deleting or relocating the target; prefer an in-place edit or wrapper.")
    if sse.get("visibility") in {"public", "public_api", "exported"} or (
        str(sse.get("max_change_kind", "")) == "CONTRACT"
    ):
        constraints.append("Preserve the public surface and existing public signatures unless explicitly authorized.")
    constraints.append(
        "Consider moving the implementation to a lower-impact owner or file when that preserves "
        "the goal and public behavior."
    )
    constraints.append(
        "Declare every intended file in the resubmitted assessment so the revised candidate is "
        "reassessed and bound to its complete file scope."
    )
    rollup = sse.get("file_fanin_rollup") or {}
    callers = rollup.get("distinct_caller_count")
    if isinstance(callers, int) and callers > 0:
        constraints.append(f"Inspect dependent code before changing this route ({callers} dependent callers).")
    symbol_fanin = sse.get("symbol_fanin") or {}
    symbol_callers = symbol_fanin.get("caller_count")
    if isinstance(symbol_callers, int) and symbol_callers > 0:
        constraints.append(
            f"Inspect direct symbol dependents before revising this route ({symbol_callers} callers)."
        )
    route: dict[str, Any] = {
        "summary": "Revise to a safer, narrower route and resubmit before editing.",
        "constraints": constraints,
    }
    return route


def render(
    result: AssessmentResult, action: CandidateAction, explanation: Explanation
) -> dict[str, Any]:
    selection = high_risk_controls.select_controls(result.high_risk_triggers)
    decision = result.recommended_decision.value
    safer_route = _safer_route(result, action)

    required_checks: list[str] = []
    if decision in {"test_first", "proceed"} and action.expected_files:
        required_checks.append("run targeted tests for the touched scope before commit")

    # 3d — make graph incompleteness visible and actionable to the model (advisory only). When the
    # graph resolved cleanly, graph_evidence is {} and this adds nothing (worked example unchanged).
    graph_evidence = result.graph_evidence or {}
    suggested_inspection: list[str] = []
    if graph_evidence:
        suggested_inspection.append(graph_evidence["reason"])
        for line in (
            graph_evidence.get("unresolved_imports", [])
            + graph_evidence.get("dynamic_imports", [])
            + graph_evidence.get("wildcard_imports", [])
        ):
            suggested_inspection.append(f"inspect import surface: {line}")
        for f in graph_evidence.get("missing_files", []):
            suggested_inspection.append(f"expected file not found in repo: {f}")
        for f in graph_evidence.get("parse_error_files", []):
            suggested_inspection.append(f"could not parse expected file: {f}")

    # M5c.5 — surface the graph-engine (codegraph) evidence-validity remediation when Gate 13 flagged
    # the fan-in evidence as untrusted (required + stale/mismatch/uninitialized/ambiguous). Empty when
    # the graph is trusted or optional, so a clean assessment carries no noise.
    fanin_validity = result.fanin_validity or {}
    if fanin_validity.get("reason"):
        suggested_inspection.append(fanin_validity["reason"])
    repo_blast = result.provenance.get("repo_blast") or {}
    repo_blast_risk_fact = (
        {
            "repo_blast_fraction": repo_blast["modify_repo_blast_fraction"],
            "repo_blast_percent": round(repo_blast["modify_repo_blast_fraction"] * 100, 2),
            "repo_blast_node_count": repo_blast["affected_node_count"],
            "repo_graph_node_count": repo_blast["repo_node_count"],
        }
        if repo_blast
        else {}
    )

    # Multi-language honesty: when we're about to PROCEED on a diff that was classified from COARSE
    # CodeGraph structure (the multi-language tier) rather than a full language-level diff, say so — a
    # coarse structural tier is NOT a verified signature check and must not be read as one. (The default
    # "unavailable" tier is the long-standing status quo and is deliberately NOT noted here.)
    structure_tier = (result.symbol_scope_evidence or {}).get("structure_tier")
    if decision == "proceed" and structure_tier in UNCERTAIN_STRUCTURE_TIERS:
        # The semantic tier proves ONE owner's signature fields, not a whole-file public-surface
        # guarantee, and the coarse tier proves even less — both keep this honesty note on proceed.
        suggested_inspection.append(
            "diff classified from CodeGraph structure (not a full language-level diff); "
            "signature-level detail was not fully verified — confirm the public surface is unchanged"
        )

    return {
        # Logical placeholder for the pure guidance packet. The store assigns the canonical
        # persisted id (assessment-row scoped) before hashing and writing the assessment.
        "guidance_packet_id": f"gp_{action.id}",
        "decision": decision,
        "risk_mode": result.risk_mode.value,
        "binding": {
            "safe_scope": {
                "files": _safe_scope_files(action),
                "edit_policy": "smallest_sufficient_edit; no broad refactor",
            },
            "risky_scope": list(_DEFAULT_RISKY_SCOPE),
            "required_checks_before_commit": required_checks,
            "required_controls": selection.required_controls,
            # Phase-1 dry-run trigger from the action flags we have (dependency upgrades). Rename /
            # broad-refactor detection is enriched later; pebra_verify enforces the preview (§9 rule 5).
            "requires_dry_run": bool(action.is_dependency_change),
        },
        "advisory": {
            "high_risk_triggers": list(result.high_risk_triggers),
            "risk_facts": {
                "risk_level": explanation.risk_level_band,
                "affected_area": explanation.affected_area,
                "confidence": explanation.confidence_band,
                **repo_blast_risk_fact,
            },
            "why": list(explanation.why),
            "suggested_inspection": suggested_inspection,
            "graph_evidence": graph_evidence,
            "fanin_validity": fanin_validity,
            "safer_route": safer_route,
            "safer_alternative": (
                safer_route["summary"] if safer_route else (
                    "make a targeted patch instead of a broad refactor"
                    if decision != "proceed"
                    else None
                )
            ),
        },
        "provenance": {
            "safe_scope": "candidate action envelope",
            "risky_scope": "policy gates + detected risk events",
            "required_checks_before_commit": "test discovery + decision",
            "required_controls": "high_risk_triggers + control blueprint selector",
            "high_risk_triggers": "symbol_diff + criticality + gates",
            "risk_facts": "risk_report + evidence discovery",
            "why": "explanation_generator",
            "graph_evidence": "blast-radius graph resolution (3c/3d)",
            "safer_route": "decision + symbol scope evidence + candidate envelope",
        },
    }
