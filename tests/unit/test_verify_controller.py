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

    def active_sanction_for_assessment(self, assessment_id):
        return None

    def invalidate_sanctions_for_assessment(self, assessment_id, reason):
        self.invalidated.append((assessment_id, reason))
        return ["sx_1"]


class FakeVerifier:
    def __init__(self, summary):
        self._summary = summary
        self.thresholds = None

    def actual_diff(self, repo_root, scope, thresholds=None):
        self.thresholds = thresholds
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


def test_newly_consequential_actual_diff_routes_inspect_first() -> None:
    # verify reads pre_edit consequential from the stored assessment (False here) and the actual
    # (consequential True from real fan-in) -> newly consequential -> inspect_first.
    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL",
        actual_consequential_symbol_changed=True,
    )
    outcome, _ = _run(summary)
    assert outcome.result.newly_consequential is True
    assert outcome.result.pre_commit_decision is Decision.INSPECT_FIRST


def test_pre_edit_consequential_suppresses_verify_re_escalation() -> None:
    # stored assessment already consequential -> verify must not re-flag the same signal.
    class ConseqStore(FakeStore):
        def load_assessment(self, assessment_id):
            s = dict(_STORED)
            s["scores"] = {"symbol_scope_evidence": {"max_change_kind": "BEHAVIORAL",
                                                     "consequential_symbol_changed": True}}
            return s

    summary = ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL",
        actual_consequential_symbol_changed=True,
    )
    outcome = vc.verify(
        "asm_1", scope="staged", completed_checks={"pytest -q src/auth": "passed"},
        repo_root="/abs/path", store=ConseqStore(),
        change_verifier=FakeVerifier(summary), contract_surface=FakeContract(),
    )
    assert outcome.result.newly_consequential is False
    assert outcome.result.pre_commit_decision is Decision.PROCEED


def test_stored_threshold_override_is_threaded_to_the_verifier() -> None:
    # Bug-2 guard: assess persists thresholds under stored["request"]["thresholds"]; verify must read
    # them and pass them to the verifier so the consequential fan-in threshold is symmetric, not the
    # hardcoded 0.90 default.
    class ThreshStore(FakeStore):
        def load_assessment(self, assessment_id):
            s = dict(_STORED)
            s["request"] = {"task": "t", "thresholds": {"consequential_symbol_fan_in_percentile": 0.80}}
            return s

    fv = FakeVerifier(ActualDiffSummary(
        current_head="abc123", changed_files=["src/auth.py"], actual_max_change_kind="BEHAVIORAL"))
    vc.verify(
        "asm_1", scope="staged", completed_checks={"pytest -q src/auth": "passed"},
        repo_root="/abs/path", store=ThreshStore(), change_verifier=fv, contract_surface=FakeContract(),
    )
    assert fv.thresholds == {"consequential_symbol_fan_in_percentile": 0.80}


def test_no_stored_request_thresholds_passes_empty_dict() -> None:
    # _STORED has no "request" key -> verify threads {} (verifier then uses the 0.90 default). No crash.
    fv = FakeVerifier(ActualDiffSummary(current_head="abc123", changed_files=["src/auth.py"],
                                        actual_max_change_kind="BEHAVIORAL"))
    _run_with_verifier(fv)
    assert fv.thresholds == {}


def _run_with_verifier(fv):
    return vc.verify(
        "asm_1", scope="staged", completed_checks={"pytest -q src/auth": "passed"},
        repo_root="/abs/path", store=FakeStore(), change_verifier=fv, contract_surface=FakeContract(),
    )


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


def test_sanction_pre_commit_controls_are_enforced() -> None:
    class SanctionedStore(FakeStore):
        def active_sanction_for_assessment(self, assessment_id):
            return {"pre_commit_required_controls": ["migration dry-run"]}

    store = SanctionedStore()
    summary = ActualDiffSummary(current_head="abc123", changed_files=["src/auth.py"])
    outcome = vc.verify(
        "asm_1",
        scope="staged",
        completed_checks={"pytest -q src/auth": "passed"},
        repo_root="/abs/path",
        store=store,
        change_verifier=FakeVerifier(summary),
        contract_surface=FakeContract(),
    )
    assert outcome.result.pre_commit_decision is Decision.TEST_FIRST
    assert "migration dry-run" in outcome.result.missing_checks


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


def test_verify_passes_stored_request_thresholds_to_change_verifier() -> None:
    stored = dict(_STORED)
    stored["request"] = {"thresholds": {"consequential_symbol_fan_in_percentile": 0.80}}

    class ThresholdStore(FakeStore):
        def load_assessment(self, assessment_id):
            return dict(stored)

    verifier = FakeVerifier(ActualDiffSummary(current_head="abc123", changed_files=["src/auth.py"]))
    vc.verify(
        "asm_1",
        scope="staged",
        completed_checks={"pytest -q src/auth": "passed"},
        repo_root="/abs/path",
        store=ThresholdStore(),
        change_verifier=verifier,
        contract_surface=FakeContract(),
    )
    assert verifier.thresholds == {"consequential_symbol_fan_in_percentile": 0.80}
