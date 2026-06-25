"""Architecture §9, plan §5 — verify_controller end-to-end over FAKE ports (no git/DB).

verify loads the stored assessment binding, compares the actual diff against the envelope via the
guardrails, persists a guardrails row, and returns the verify decision.
"""

from __future__ import annotations

from pebra.app import verify_controller as vc
from pebra.core.constants import Decision
from pebra.core.models import ActualDiffSummary, ContractSurfaceFindings

_STORED = {
    "decision": "proceed",
    "assessed_commit": "abc123",
    "repo_id": "repo_local_example",
    "repo_root": "/abs/path",
    "scores": {"symbol_scope_evidence": {"max_change_kind": "BEHAVIORAL"}},
    "model_guidance_packet": {
        "binding": {
            "safe_scope": {"files": ["src/auth.py", "src/auth/__tests__/**"]},
            "risky_scope": [
                {"change": "dependency upgrades", "action": "requires_reassessment",
                 "signal": "dependency_changed"},
            ],
            "required_checks_before_commit": ["pytest -q src/auth"],
        }
    },
}


class FakeStore:
    def __init__(self):
        self.guardrails = []
        self.invalidated = []

    def load_assessment(self, assessment_id):
        return dict(_STORED)

    def persist_guardrails(self, assessment_id, guardrails):
        self.guardrails.append((assessment_id, guardrails))
        return f"pag_{len(self.guardrails)}"

    def invalidate_sanctions_for_assessment(self, assessment_id, reason):
        self.invalidated.append((assessment_id, reason))
        return ["sx_1"]


class FakeVerifier:
    def __init__(self, summary):
        self._summary = summary

    def actual_diff(self, repo_root, scope):
        return self._summary


class FakeContract:
    def __init__(self, changes=()):
        self._changes = list(changes)

    def contract_findings(self, repo_root, changed_files):
        return ContractSurfaceFindings(changes=self._changes)


def _run(summary, *, completed_checks=None, contract_changes=()):
    store = FakeStore()
    if completed_checks is None:
        completed_checks = {"pytest -q src/auth": "passed"}
    outcome = vc.verify(
        "asm_1",
        scope="staged",
        completed_checks=completed_checks,
        repo_root="/abs/path",
        store=store,
        change_verifier=FakeVerifier(summary),
        contract_surface=FakeContract(contract_changes),
    )
    return outcome, store


def test_within_envelope_diff_proceeds_and_persists() -> None:
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL"
    )
    outcome, store = _run(summary)
    assert outcome.result.pre_commit_decision is Decision.PROCEED
    assert outcome.guardrails_id == "pag_1"
    assert len(store.guardrails) == 1
    assert store.invalidated == []  # no drift -> no sanction invalidation


def test_drift_invalidates_bound_sanctions() -> None:
    # AD-26: scope drift on a verified assessment invalidates its active sanction(s).
    summary = ActualDiffSummary(
        current_head="abc123",
        changed_files=["src/auth.py", "src/payments/charge.py"],
        actual_max_change_kind="BEHAVIORAL",
    )
    outcome, store = _run(summary)
    assert outcome.result.scope_drift_detected is True
    assert store.invalidated == [("asm_1", store.invalidated[0][1])]
    assert outcome.invalidated_sanctions == ["sx_1"]


def test_stale_evidence_invalidates_sanctions() -> None:
    summary = ActualDiffSummary(current_head="deadbeef", changed_files=["src/auth.py"])
    outcome, store = _run(summary)
    assert store.invalidated  # stale evidence is drift -> invalidate


def test_classification_failure_invalidates_sanctions() -> None:
    # UNKNOWN + reclassification_attempted = "couldn't prove envelope compliance" -> a bound sanction
    # must not survive (AD-26), even though scope_drift and symbol_change_mismatch are both false.
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"],
        actual_max_change_kind="UNKNOWN", reclassification_attempted=True,
    )
    outcome, store = _run(summary)
    assert outcome.result.pre_commit_decision is Decision.INSPECT_FIRST
    assert outcome.result.classification_failed is True
    assert store.invalidated  # sanction invalidated on classification failure


def test_measured_benefit_credited_when_complexity_reduced() -> None:
    # negative complexity_delta (simpler code) -> positive measured maintainability benefit
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL",
        measured_benefit_deltas={"complexity_delta": -3.0},
    )
    outcome, _ = _run(summary)
    assert outcome.measured_benefit > 0.0


def test_no_measured_benefit_when_no_deltas() -> None:
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL"
    )
    outcome, _ = _run(summary)
    assert outcome.measured_benefit == 0.0


def test_out_of_envelope_file_is_caught() -> None:
    summary = ActualDiffSummary(
        current_head="abc123",
        changed_files=["src/auth.py", "src/payments/charge.py"],
        actual_max_change_kind="BEHAVIORAL",
    )
    outcome, _ = _run(summary)
    assert outcome.result.scope_drift_detected is True
    assert "src/payments/charge.py" in outcome.result.unexpected_files
    assert outcome.result.pre_commit_decision is Decision.INSPECT_FIRST


def test_stale_head_is_caught() -> None:
    summary = ActualDiffSummary(current_head="deadbeef", changed_files=["src/auth.py"])
    outcome, _ = _run(summary)
    assert outcome.result.evidence_freshness == "stale"
    assert outcome.result.pre_commit_decision is Decision.INSPECT_FIRST


def test_dependency_signal_triggers_risky_scope_reassessment() -> None:
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], dependency_changed=True
    )
    outcome, _ = _run(summary)
    assert "requires_reassessment" in outcome.result.risky_scope_actions_triggered
    assert outcome.result.pre_commit_decision is Decision.ASK_HUMAN


def test_contract_change_signal_routes_ask_human() -> None:
    summary = ActualDiffSummary(current_head="abc123", changed_files=["src/auth.py"])
    outcome, _ = _run(summary, contract_changes=["public_api_break:validate_login"])
    assert "public_api_break:validate_login" in outcome.result.contract_surface_changes
    assert outcome.result.pre_commit_decision is Decision.ASK_HUMAN


def test_missing_check_routes_test_first() -> None:
    summary = ActualDiffSummary(current_head="abc123", changed_files=["src/auth.py"])
    outcome, _ = _run(summary, completed_checks={})
    assert outcome.result.pre_commit_decision is Decision.TEST_FIRST


def test_requires_dry_run_from_binding_routes_inspect_first_without_preview() -> None:
    # binding declares requires_dry_run; no preview supplied -> rule 5 fires (inspect_first)
    stored = dict(_STORED)
    stored["model_guidance_packet"] = {
        "binding": {
            "safe_scope": {"files": ["src/auth.py"]},
            "risky_scope": [],
            "required_checks_before_commit": [],
            "requires_dry_run": True,
        }
    }

    class DryRunStore(FakeStore):
        def load_assessment(self, assessment_id):
            return dict(stored)

    store = DryRunStore()
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL"
    )
    outcome = vc.verify(
        "asm_1", scope="staged", completed_checks={}, repo_root="/abs/path",
        store=store, change_verifier=FakeVerifier(summary), contract_surface=FakeContract(),
    )
    assert outcome.result.dry_run_required is True
    assert outcome.result.pre_commit_decision is Decision.INSPECT_FIRST


def test_dry_run_preview_present_clears_the_dry_run_requirement() -> None:
    stored = dict(_STORED)
    stored["model_guidance_packet"] = {
        "binding": {
            "safe_scope": {"files": ["src/auth.py"]},
            "risky_scope": [],
            "required_checks_before_commit": [],
            "requires_dry_run": True,
        }
    }

    class DryRunStore(FakeStore):
        def load_assessment(self, assessment_id):
            return dict(stored)

    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL"
    )
    outcome = vc.verify(
        "asm_1", scope="staged", completed_checks={}, dry_run_preview_present=True,
        repo_root="/abs/path", store=DryRunStore(),
        change_verifier=FakeVerifier(summary), contract_surface=FakeContract(),
    )
    assert outcome.result.dry_run_required is False
    assert outcome.result.pre_commit_decision is Decision.PROCEED
