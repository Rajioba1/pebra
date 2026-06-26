"""3c-3 — the controller feeds graph incompleteness into the decision.

graph_uncertainty_score (from the blast walker) caps evidence_quality (one of the six edit_confidence
factors): evidence_quality_effective = max(0, supplied - score). That lowers edit_confidence through
the existing geometric mean, which can nudge a borderline case across the low-confidence threshold
into inspect_first via the EXISTING gate 8 — no new gate. A fully resolved graph (score 0.0) changes
nothing, so the worked example stays byte-identical.
"""

from __future__ import annotations

import pytest

from pebra.app import assess_controller as ac
from pebra.core import models as m
from pebra.core.constants import Decision
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
    """Worked-example evidence (evidence_quality supplied at 0.78)."""

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


class BorderlineEvidence:
    """Benign, low-risk scenario with all six confidence factors at 0.52 (edit_confidence ~0.52,
    just above the 0.50 low threshold). A graph-uncertainty penalty should tip it below -> gate 8."""

    def gather_evidence(self, request, action, repo_root):
        return m.EvidenceBundle(
            events=[{"event": "test_regression", "p_event": 0.02, "elicited_disutility": 0.10}],
            p_success=0.52,
            immediate_benefit=0.70,
            review_cost=0.10,
            criticality_stage="C0",
            criticality_value=0.10,
            edit_confidence_factors={
                "p_success": 0.52, "evidence_quality": 0.52, "testability": 0.52,
                "reversibility": 0.52, "source_reliability": 0.52, "scope_control": 0.52,
            },
            thresholds=_THRESHOLDS,
            variance_breakdown={
                "p_success": 0.0004, "benefit": 0.0002, "event_losses": 0.0001,
                "review_cost": 0.0002, "scenario_variance": 0.0001,
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
    def __init__(
        self, score: float = 0.0, reason: str = "", unresolved=(), dynamic=(), missing=()
    ) -> None:
        self._score = score
        self._reason = reason
        self._unresolved = tuple(unresolved)
        self._dynamic = tuple(dynamic)
        self._missing = tuple(missing)

    def blast(self, action, repo_root):
        return m.BlastEvidence(
            direct_count=2, transitive_count=1,
            graph_uncertainty_score=self._score, graph_uncertainty_reason=self._reason,
            unresolved_imports=self._unresolved, dynamic_imports=self._dynamic,
            missing_files=self._missing,
        )


class FakeSanction:
    def active_sanction(self, repo_id, action):
        return None

    def create_sanction(self, repo_id, sanction):
        return "sx_1"


class FakeRegistry:
    def resolve(self, start_path):
        return RepoMetadata(repo_id="repo_local_example", repo_root="/abs/path/to/example-repo")


class FakeStore:
    def persist_assessment(self, result, request_payload, predictions=None):
        return "asm_1"

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


def _build_input(blast, evidence=None):
    req = _request()
    return ac._build_input(
        req, req.candidate_actions[0], "repo", "/root", _THRESHOLDS,
        evidence_provider=evidence or FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=blast,
        sanction_port=FakeSanction(),
    )


def _run(blast, evidence=None):
    return ac.assess(
        _request(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=evidence or FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=blast,
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=FakeStore(),
    ).recommended_result


def test_build_input_caps_evidence_quality_by_uncertainty() -> None:
    inp = _build_input(FakeBlast(score=0.20))
    assert inp.edit_confidence_factors["evidence_quality"] == pytest.approx(0.58)  # 0.78 - 0.20


def test_build_input_full_confidence_leaves_evidence_quality_untouched() -> None:
    inp = _build_input(FakeBlast(score=0.0))
    assert inp.edit_confidence_factors["evidence_quality"] == pytest.approx(0.78)


def test_uncertainty_penalty_never_drives_evidence_quality_negative() -> None:
    inp = _build_input(FakeBlast(score=0.25), evidence=BorderlineEvidence())  # 0.52 - 0.25
    assert inp.edit_confidence_factors["evidence_quality"] == pytest.approx(0.27)
    assert inp.edit_confidence_factors["evidence_quality"] >= 0.0


def test_uncertainty_lowers_edit_confidence_end_to_end() -> None:
    clean = _run(FakeBlast(0.0)).scores["edit_confidence"]
    penalized = _run(FakeBlast(0.20)).scores["edit_confidence"]
    assert penalized < clean
    assert round(clean, 2) == 0.83  # worked example preserved at full graph confidence


def test_clean_graph_preserves_worked_example_decision() -> None:
    r = _run(FakeBlast(0.0))
    assert r.recommended_decision is Decision.PROCEED
    assert round(r.scores["edit_confidence"], 2) == 0.83


def test_high_uncertainty_tips_borderline_case_to_inspect_first() -> None:
    clean = _run(FakeBlast(0.0), evidence=BorderlineEvidence())
    penalized = _run(FakeBlast(0.25), evidence=BorderlineEvidence())
    assert clean.scores["edit_confidence"] >= 0.50
    assert penalized.scores["edit_confidence"] < 0.50
    assert clean.recommended_decision is not Decision.INSPECT_FIRST
    assert penalized.recommended_decision is Decision.INSPECT_FIRST


# --- 3d: provenance reaches the result and the model-guidance packet ---

def test_graph_evidence_block_populated_on_result() -> None:
    blast = FakeBlast(
        score=0.10, reason="Graph evidence incomplete: 1 unresolved internal import(s).",
        unresolved=("src/auth.py: billing.legacy",),
    )
    r = _run(blast)
    ge = r.graph_evidence
    assert ge["score"] == pytest.approx(0.10)
    assert "src/auth.py: billing.legacy" in ge["unresolved_imports"]
    assert ge["reason"].startswith("Graph evidence incomplete")


def test_guidance_packet_surfaces_uncertainty_reason_and_names() -> None:
    blast = FakeBlast(
        score=0.10, reason="Graph evidence incomplete: 1 dynamic import(s).",
        dynamic=("src/auth.py: plugins.x",), missing=("ghost.py",),
    )
    packet = _run(blast).model_guidance_packet
    inspection = " ".join(packet["advisory"]["suggested_inspection"]).lower()
    assert "incomplete" in inspection
    ge = packet["advisory"]["graph_evidence"]
    assert "src/auth.py: plugins.x" in ge["dynamic_imports"]
    assert "ghost.py" in ge["missing_files"]


def test_clean_graph_leaves_guidance_uncertainty_empty() -> None:
    packet = _run(FakeBlast(0.0)).model_guidance_packet
    assert packet["advisory"]["graph_evidence"] == {}
    assert packet["advisory"]["suggested_inspection"] == []
    assert _run(FakeBlast(0.0)).graph_evidence == {}
