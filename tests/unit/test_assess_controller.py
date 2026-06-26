"""Architecture §11, plan §5 — assess_controller end-to-end over FAKE ports (no FS/DB/subprocess).

This is the controller pipeline test: request -> ports gather evidence -> engine -> render/persist.
It must reproduce the spec §10 worked example end-to-end.
"""

from __future__ import annotations

import pytest

from pebra.app import assess_controller as ac
from pebra.core import models as m
from pebra.core.constants import Decision, RiskMode
from pebra.ports.repository_registry_port import RepoMetadata

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45,
    "c3_max_expected_loss_without_human": 0.20,
    "max_utility_sd_without_human": 0.20,
    "high_edit_confidence": 0.75,
    "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}


class FakeEvidence:
    def gather_evidence(self, request, action, repo_root):
        return m.EvidenceBundle(
            events=[
                {"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40},
                {"event": "public_api_break", "p_event": 0.03, "elicited_disutility": 0.80},
                {"event": "security_sensitive_change", "p_event": 0.04, "elicited_disutility": 0.90},
            ],
            p_success=0.74,
            immediate_benefit=0.82,
            review_cost=0.12,
            criticality_stage="C3",
            criticality_value=0.80,
            edit_confidence_factors={
                "p_success": 0.74, "evidence_quality": 0.78, "testability": 0.80,
                "reversibility": 0.92, "source_reliability": 0.86, "scope_control": 0.92,
            },
            thresholds=_THRESHOLDS,
            variance_breakdown={
                "p_success": 0.0016, "benefit": 0.0004, "event_losses": 0.0009,
                "review_cost": 0.0004, "scenario_variance": 0.0003,
            },
            benefit_delta_evidence=m.BenefitDeltaEvidence(source_type="projected"),
        )


class FakeSymbolDiff:
    def symbol_diff(self, action, repo_root):
        return m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/auth.py::validate_login"],
            max_change_kind="BEHAVIORAL",
            visibility="internal",
            symbol_fan_in_percentile=0.42,
            consequential_symbol_changed=False,
        )


class FakeBlast:
    def blast(self, action, repo_root):
        return m.BlastEvidence(direct_count=2, transitive_count=1)


class FakeSanction:
    def active_sanction(self, repo_id, action):
        return None

    def create_sanction(self, repo_id, sanction):
        return "sx_1"


class FakeRegistry:
    def resolve(self, start_path):
        return RepoMetadata(repo_id="repo_local_example", repo_root="/abs/path/to/example-repo")


class FakeStore:
    def __init__(self):
        self.persisted = []

    def persist_assessment(self, result, request_payload, predictions=None):
        self.persisted.append((result, request_payload, predictions or []))
        return f"asm_{len(self.persisted)}"

    def validate_chain(self):
        return True


def _request():
    return m.AssessmentRequest.single_action(
        task="Fix failing login validation",
        action_id="a1",
        label="Patch validate_login only",
        action_type="edit",
        affected_symbols=["src/auth.py::validate_login"],
        expected_files=["src/auth.py"],
    )


def _run():
    store = FakeStore()
    outcome = ac.assess(
        _request(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
    )
    return outcome, store


def test_controller_reproduces_worked_example_decision() -> None:
    outcome, _ = _run()
    r = outcome.recommended_result
    assert r.recommended_decision is Decision.PROCEED
    assert r.requires_confirmation is True
    assert r.risk_mode is RiskMode.SENSITIVE_CONTEXT


def test_controller_reproduces_worked_example_numbers() -> None:
    outcome, _ = _run()
    s = outcome.recommended_result.scores
    assert s["expected_loss"] == pytest.approx(0.10)
    assert s["expected_utility"] == pytest.approx(0.3868)
    assert s["utility_sd"] == pytest.approx(0.06)
    assert s["rau"] == pytest.approx(0.31)
    assert round(s["edit_confidence"], 2) == 0.83
    assert s["risk_budget_used"] == pytest.approx(0.50)


def test_controller_renders_card_fields_and_packet() -> None:
    outcome, _ = _run()
    assert outcome.recommended_explanation.risk_level_band == "Moderate"
    assert outcome.recommended_explanation.value_after_risk_band == "Positive"
    assert outcome.recommended_result.model_guidance_packet["decision"] == "proceed"


def test_controller_persists_and_returns_repo_scope() -> None:
    outcome, store = _run()
    assert outcome.assessment_id == "asm_1"
    assert outcome.repo_id == "repo_local_example"
    assert len(store.persisted) == 1


def test_controller_rejects_invalid_request() -> None:
    from pebra.core.request_validator import RequestValidationError
    bad = m.AssessmentRequest(task="", candidate_actions=[])
    with pytest.raises(RequestValidationError):
        ac.assess(
            bad, thresholds=_THRESHOLDS, start_path="/x",
            evidence_provider=FakeEvidence(), symbol_diff_provider=FakeSymbolDiff(),
            blast_provider=FakeBlast(), sanction_port=FakeSanction(),
            repository_registry=FakeRegistry(), store=FakeStore(),
        )
