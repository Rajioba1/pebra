"""verify_controller (Architecture §9, plan §5) — the post-edit verify use case.

Loads the stored assessment's binding (the model guidance packet = pre-edit autonomy envelope),
gathers the actual diff + contract-surface findings via ports, runs the pure post-assessment
guardrails, persists a guardrails row, and returns the verify decision. Imports only core/ + ports/.

The engine never fetches: the controller pre-fetches every input and hands the pure guardrails a
fully-populated GuardrailInput.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pebra.core import benefit_model
from pebra.core import post_assessment_guardrails as pag
from pebra.core.post_assessment_guardrails import GuardrailInput, GuardrailResult
from pebra.ports.change_verifier_port import ChangeVerifier
from pebra.ports.contract_surface_port import ContractSurfaceProvider
from pebra.ports.store_port import StorePort


@dataclass
class VerifyOutcome:
    result: GuardrailResult
    guardrails_id: str
    repo_id: str
    invalidated_sanctions: list[str] = field(default_factory=list)
    measured_benefit: float = 0.0  # post-edit measured maintainability benefit (AD-29 feeds learning)
    # The raw RCA deltas behind measured_benefit ({complexity_delta, maintainability_index_delta}, or {}
    # when nothing was measured). Surfaced on the verify JSON boundary — not dashboard-only.
    measured_benefit_deltas: dict[str, float] = field(default_factory=dict)


def _triggered_signals(actual, contract_changes: list[str]) -> set[str]:
    signals: set[str] = set()
    if actual.dependency_changed:
        signals.add("dependency_changed")
    if actual.schema_changed:
        signals.add("schema_changed")
    if actual.migration_changed:
        signals.add("migration_changed")
    if contract_changes:
        signals.add("contract_change")
    return signals


def _result_to_dict(result: GuardrailResult) -> dict[str, Any]:
    d = asdict(result)
    d["pre_commit_decision"] = result.pre_commit_decision.value
    return d


def verify(
    assessment_id: str,
    *,
    scope: str = "staged",
    completed_checks: dict[str, str] | None = None,
    dry_run_preview_present: bool = False,
    repo_root: str,
    store: StorePort,
    change_verifier: ChangeVerifier,
    contract_surface: ContractSurfaceProvider,
) -> VerifyOutcome:
    stored = store.load_assessment(assessment_id)
    binding = stored["model_guidance_packet"]["binding"]
    safe_scope_files = list(binding["safe_scope"]["files"])
    risky_scope = list(binding.get("risky_scope", []))
    required_checks = list(binding.get("required_checks_before_commit", []))
    sanction = store.active_sanction_for_assessment(assessment_id)
    if sanction:
        for check in sanction.get("pre_commit_required_controls", []):
            if check not in required_checks:
                required_checks.append(check)
    requires_dry_run = bool(binding.get("requires_dry_run", False))
    sse = stored["scores"]["symbol_scope_evidence"]
    pre_edit_kind = sse["max_change_kind"]
    pre_edit_consequential = bool(sse.get("consequential_symbol_changed", False))
    pre_edit_structure_tier = str(sse.get("structure_tier", "unavailable"))
    stored_thresholds = dict((stored.get("request") or {}).get("thresholds") or {})
    assessed_commit = stored.get("assessed_commit")

    actual = change_verifier.actual_diff(repo_root, scope, thresholds=stored_thresholds)
    contract = contract_surface.contract_findings(repo_root, actual.changed_files)

    inp = GuardrailInput(
        assessed_commit=assessed_commit,
        current_head=actual.current_head,
        safe_scope_files=safe_scope_files,
        changed_files=list(actual.changed_files),
        dependency_changed=actual.dependency_changed,
        schema_changed=actual.schema_changed,
        migration_changed=actual.migration_changed,
        pre_edit_max_change_kind=pre_edit_kind,
        actual_max_change_kind=actual.actual_max_change_kind,
        actual_changed_symbols=list(actual.actual_changed_symbols),
        pre_edit_consequential=pre_edit_consequential,
        actual_consequential=actual.actual_consequential_symbol_changed,
        contract_surface_changes=list(contract.changes),
        risky_scope=risky_scope,
        triggered_signals=_triggered_signals(actual, list(contract.changes)),
        required_checks=required_checks,
        completed_checks=dict(completed_checks or {}),
        requires_dry_run=requires_dry_run,
        dry_run_preview_present=dry_run_preview_present,
        reclassification_attempted=actual.reclassification_attempted,
        pre_edit_structure_tier=pre_edit_structure_tier,
        actual_structure_tier=actual.actual_structure_tier,
    )
    result = pag.evaluate(inp)

    # Measured post-edit benefit deltas (Architecture §9 / spec §6): the actual diff's maintainability
    # change, credited in `measured` mode. Recorded for AD-29 benefit calibration; does not gate verify.
    measured = benefit_model.resolve_benefit(
        immediate_benefit=0.0,
        deltas=actual.measured_benefit_deltas,
        source_type="measured",
        future_change_exposure=1.0 if actual.measured_benefit_deltas else 0.0,
    )
    guardrails_dict = _result_to_dict(result)
    guardrails_dict["measured_benefit"] = measured.benefit
    guardrails_dict["measured_benefit_deltas"] = dict(actual.measured_benefit_deltas)
    guardrails_id = store.persist_guardrails(assessment_id, guardrails_dict)

    # AD-26: scope/evidence/symbol-change drift invalidates any sanction bound to this assessment —
    # a controlled-high-risk approval is only valid while the edit stays in the approved profile.
    invalidated: list[str] = []
    drift = (
        result.scope_drift_detected
        or result.symbol_change_mismatch
        or result.evidence_freshness != "fresh"
        or result.classification_failed  # couldn't prove the sanctioned profile still holds
    )
    if drift:
        invalidated = store.invalidate_sanctions_for_assessment(
            assessment_id, f"verify drift: {result.pre_commit_decision.value}"
        )

    return VerifyOutcome(
        result=result,
        guardrails_id=guardrails_id,
        repo_id=stored.get("repo_id", ""),
        invalidated_sanctions=invalidated,
        measured_benefit=measured.benefit,
        measured_benefit_deltas=dict(actual.measured_benefit_deltas),
    )
