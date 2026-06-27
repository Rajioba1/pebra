"""post_assessment_guardrails (Architecture §9, AD-11/AD-27) — pure, stdlib only.

The autonomy-envelope rule: PEBRA may let an agent proceed on a branch, but only if the final diff
still matches the approved risk envelope. The model guidance packet is the pre-edit face of that
envelope; this module is the post-edit enforcement. It receives already-fetched data (stored binding,
current diff/HEAD, reclassified actual symbol diff, contract-surface findings, completed checks) and
returns a ``GuardrailResult``. It must NOT call git, read the filesystem, import sqlite3, or inspect
the repo — that I/O belongs in adapters behind ChangeVerifier / ContractSurfaceProvider / StorePort.

Hard failures map to the existing five decisions only (no new enum). When several rules fire, the most
severe decision wins (reject > ask_human > test_first > inspect_first > proceed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from pebra.core import change_classifier
from pebra.core.constants import ChangeKind, Decision

# Signals that a dedicated rule already escalates (broad drift + contract surface). A default
# `requires_reassessment` risky_scope entry on one of these is deduped to avoid a contradictory
# weaker label/reason alongside the dedicated rule's headline decision.
_DEDICATED_RULE_SIGNALS = frozenset(
    {"dependency_changed", "schema_changed", "migration_changed", "contract_change"}
)

# Decision precedence for "most severe wins" (reject is most severe).
_DECISION_RANK = {
    Decision.PROCEED: 0,
    Decision.INSPECT_FIRST: 1,
    Decision.TEST_FIRST: 2,
    Decision.ASK_HUMAN: 3,
    Decision.REJECT: 4,
}


@dataclass
class GuardrailInput:
    assessed_commit: str | None
    current_head: str | None
    safe_scope_files: list[str]
    changed_files: list[str]
    dependency_changed: bool
    schema_changed: bool
    migration_changed: bool
    pre_edit_max_change_kind: str
    actual_max_change_kind: str
    actual_changed_symbols: list[str]
    contract_surface_changes: list[str]
    risky_scope: list[dict[str, Any]]
    triggered_signals: set[str]
    required_checks: list[str]
    completed_checks: dict[str, str]
    requires_dry_run: bool = False
    dry_run_preview_present: bool = False
    policy_forbidden: bool = False
    reclassification_attempted: bool = False
    # A1 (M5c.5): consequence verdict pre-edit (from the stored assessment) vs post-edit (reclassifier
    # with real fan-in). Escalate only when NEWLY consequential — never re-flag what assess approved.
    pre_edit_consequential: bool = False
    actual_consequential: bool = False


@dataclass
class GuardrailResult:
    evidence_freshness: str
    assessed_commit: str | None
    current_head: str | None
    scope_drift_detected: bool
    unexpected_files: list[str]
    pre_edit_symbol_diff_summary: str
    actual_symbol_diff_summary: str
    symbol_change_mismatch: bool
    contract_surface_changes: list[str]
    dry_run_required: bool
    classification_failed: bool
    pre_commit_decision: Decision
    reasons: list[str] = field(default_factory=list)
    safe_scope_status: str = "ok"
    risky_scope_triggered: list[str] = field(default_factory=list)
    risky_scope_actions_triggered: list[str] = field(default_factory=list)
    completed_checks: dict[str, str] = field(default_factory=dict)
    missing_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    necessity_evidence_present: bool = False
    newly_consequential: bool = False  # A1: post-edit fan-in/scope made it consequential, assess didn't
    verify_decision: str = "proceed"


def _file_part(scope_entry: str) -> str:
    """Strip a ``::symbol`` suffix so symbol-scoped envelope entries match on their file."""
    return scope_entry.split("::", 1)[0]


def _is_covered(path: str, safe_scope_files: list[str]) -> bool:
    # fnmatch follows each platform's filesystem case semantics (case-sensitive on Linux,
    # case-insensitive on Windows). That is the fail-safe choice for a security gate: we must NOT
    # lowercase, because on case-sensitive filesystems "src/Auth.py" and "src/auth.py" are distinct
    # files and case-folding would let an out-of-scope file slip through as "covered".
    return any(fnmatch(path, _file_part(p)) for p in safe_scope_files)


def _kind_severity(kind: str) -> int:
    try:
        return change_classifier.severity(ChangeKind(kind))
    except ValueError:
        return change_classifier.severity(ChangeKind.UNKNOWN)


def evaluate(inp: GuardrailInput) -> GuardrailResult:
    reasons: list[str] = []
    candidates: list[Decision] = [Decision.PROCEED]

    # --- 1. Evidence freshness ---
    if inp.assessed_commit is None or inp.current_head is None:
        evidence_freshness = "unknown"
        # "cannot verify freshness" is not "safe to proceed": route to inspect_first.
        reasons.append(
            "Evidence freshness could not be verified (missing assessed/current commit); "
            "inspect before autonomous proceed."
        )
        candidates.append(Decision.INSPECT_FIRST)
    elif inp.assessed_commit == inp.current_head:
        evidence_freshness = "fresh"
    else:
        evidence_freshness = "stale"
        reasons.append(
            f"Evidence is stale: HEAD moved from {inp.assessed_commit} to {inp.current_head}; "
            "re-assess before autonomous proceed."
        )
        candidates.append(Decision.INSPECT_FIRST)

    # --- 2. Actual diff vs planned scope ---
    unexpected = [f for f in inp.changed_files if not _is_covered(f, inp.safe_scope_files)]
    broad_drift = inp.dependency_changed or inp.schema_changed or inp.migration_changed
    scope_drift = bool(unexpected) or broad_drift
    # safe_scope (spec §12.3.1) covers files AND dependencies/schema/migration, so broad drift is a
    # safe-scope violation too — otherwise the card could show "Scope Drift: yes" with "Safe Scope: ok".
    safe_scope_status = "violated" if (unexpected or broad_drift) else "ok"
    if unexpected:
        reasons.append(f"Files outside the approved safe scope were changed: {unexpected}.")
        candidates.append(Decision.INSPECT_FIRST)
    if broad_drift:
        reasons.append("A dependency/schema/migration change is outside the reviewed envelope.")
        candidates.append(Decision.ASK_HUMAN)

    # --- 3. Post-edit symbol reclassification (AD-27) ---
    # A mismatch means the actual change is KNOWN to be strictly more severe than the pre-edit
    # classification. UNKNOWN means "couldn't reclassify" — not "more severe" — so it does not by
    # itself escalate (the other signals still apply). Phase 2's real AST-diff reclassification
    # replaces the UNKNOWN default with a concrete kind.
    # CAUTION (Phase 2): change_classifier ranks UNKNOWN severity ABOVE BEHAVIORAL (conservative for
    # the pre-edit classifier). Do not remove the UNKNOWN guard below without revisiting that rank, or
    # any BEHAVIORAL→UNKNOWN pair would always flag a mismatch.
    symbol_change_mismatch = inp.actual_max_change_kind != ChangeKind.UNKNOWN.value and (
        _kind_severity(inp.actual_max_change_kind) > _kind_severity(inp.pre_edit_max_change_kind)
    )
    if symbol_change_mismatch:
        scope_drift = True
        reasons.append(
            f"Actual change ({inp.actual_max_change_kind}) is more severe than the pre-edit "
            f"classification ({inp.pre_edit_max_change_kind}); re-assess."
        )
        candidates.append(Decision.ASK_HUMAN)

    # --- 3b. Unclassifiable actual diff (couldn't prove envelope compliance) ---
    # If we attempted to reclassify changed Python files but the result is UNKNOWN (syntax error /
    # unparseable diff), PEBRA cannot prove the edit stayed inside the symbol envelope. Unknown is
    # NOT "safe" — escalate. (Pure non-code changes set reclassification_attempted=False and are
    # governed by scope drift alone, so an in-scope docs edit is not needlessly escalated.)
    classification_failed = (
        inp.actual_max_change_kind == ChangeKind.UNKNOWN.value
        and inp.reclassification_attempted
    )
    if classification_failed:
        reasons.append(
            "Actual diff included Python files that could not be classified (syntax error or "
            "unparseable diff); cannot prove envelope compliance."
        )
        candidates.append(Decision.INSPECT_FIRST)

    # --- 3c. Newly-consequential post-edit change (A1, AD-27) ---
    # The actual edit is consequential by post-edit per-symbol fan-in/scope evidence that the pre-edit
    # assessment did NOT flag (e.g. the edit landed in a higher-fan-in symbol than planned). This is a
    # softer signal than a kind-severity increase (3) — the change KIND didn't escalate — so it routes
    # to inspect_first, not ask_human. Only fires when NEWLY consequential, so an already-approved
    # consequential change is not re-flagged. Stage-independent: verify is envelope compliance.
    newly_consequential = inp.actual_consequential and not inp.pre_edit_consequential
    if newly_consequential:
        reasons.append(
            "Actual change is consequential by post-edit fan-in/scope evidence that the pre-edit "
            "assessment did not flag; inspect before autonomous proceed."
        )
        candidates.append(Decision.INSPECT_FIRST)

    # --- 4. Contract surface changes ---
    if inp.contract_surface_changes:
        reasons.append(f"Contract-surface changes detected: {inp.contract_surface_changes}.")
        candidates.append(Decision.ASK_HUMAN)

    # --- risky_scope binding enforcement (actions from triggered entries) ---
    # Signals already escalated by a dedicated rule above (broad drift → ask_human, contract surface
    # → ask_human). For these, a default `requires_reassessment` entry is recorded as triggered but
    # does NOT add a duplicate (weaker) reason/candidate — the dedicated rule is the headline
    # authority. Stronger actions (forbidden/avoid_unless_required) are always enforced.
    risky_triggered: list[str] = []
    risky_actions: list[str] = []
    necessity_present = False
    for entry in inp.risky_scope:
        signal = entry.get("signal")
        if signal and signal in inp.triggered_signals:
            action = entry.get("action", "requires_reassessment")
            risky_triggered.append(entry.get("change", signal))
            risky_actions.append(action)
            if action == "forbidden":
                reasons.append(f"Forbidden change touched: {entry.get('change')}.")
                candidates.append(Decision.REJECT)
            elif action == "avoid_unless_required":
                necessity_present = bool(entry.get("necessity_evidence"))
                if not necessity_present:
                    reasons.append(
                        f"avoid_unless_required change touched without necessity evidence: "
                        f"{entry.get('change')}."
                    )
                    candidates.append(Decision.ASK_HUMAN)
            elif signal in _DEDICATED_RULE_SIGNALS:
                continue  # already escalated by a dedicated rule; don't double-report
            else:  # requires_reassessment for a non-dedicated/custom signal
                reasons.append(f"requires_reassessment change touched: {entry.get('change')}.")
                candidates.append(Decision.INSPECT_FIRST)

    # --- 5. Dry-run refactor check ---
    dry_run_required = inp.requires_dry_run and not inp.dry_run_preview_present
    if dry_run_required:
        reasons.append("A rename/public-API/dependency/broad-refactor edit needs an impact preview.")
        candidates.append(Decision.INSPECT_FIRST)

    # --- required checks ---
    missing = [c for c in inp.required_checks if c not in inp.completed_checks]
    failed = [c for c, status in inp.completed_checks.items() if status == "failed"]
    if missing:
        reasons.append(f"Required pre-commit checks not yet run: {missing}.")
        candidates.append(Decision.TEST_FIRST)
    if failed:
        reasons.append(f"Required pre-commit checks failed: {failed}.")
        candidates.append(Decision.ASK_HUMAN)

    # --- policy ---
    if inp.policy_forbidden:
        reasons.append("Policy violation at the commit boundary.")
        candidates.append(Decision.REJECT)

    decision = max(candidates, key=lambda d: _DECISION_RANK[d])

    return GuardrailResult(
        evidence_freshness=evidence_freshness,
        assessed_commit=inp.assessed_commit,
        current_head=inp.current_head,
        scope_drift_detected=scope_drift,
        unexpected_files=unexpected,
        pre_edit_symbol_diff_summary=inp.pre_edit_max_change_kind,
        actual_symbol_diff_summary=inp.actual_max_change_kind,
        symbol_change_mismatch=symbol_change_mismatch,
        contract_surface_changes=list(inp.contract_surface_changes),
        dry_run_required=dry_run_required,
        classification_failed=classification_failed,
        pre_commit_decision=decision,
        reasons=reasons,
        safe_scope_status=safe_scope_status,
        risky_scope_triggered=risky_triggered,
        risky_scope_actions_triggered=risky_actions,
        completed_checks=dict(inp.completed_checks),
        missing_checks=missing,
        failed_checks=failed,
        necessity_evidence_present=necessity_present,
        newly_consequential=newly_consequential,
        verify_decision=decision.value,
    )
