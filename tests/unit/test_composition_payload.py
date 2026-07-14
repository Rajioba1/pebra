from pebra.app.assess_controller import AssessmentOutcome, ScoredAction
from pebra import composition
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult, CandidateAction


def test_assess_payload_exposes_applied_snapshot_provenance_when_present() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.INSPECT_FIRST,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={"rau": -0.1},
        repo_id="r",
        repo_root="/repo",
        provenance={
            "provider": "pebra",
            "source_type": "derived",
            "applied_snapshot_provenance": {"snapshot_id": "snap_1"},
        },
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="Low",
            value_after_risk_band="Positive",
            confidence_band="high",
            confidence_percent=90,
            code_sensitivity_label="Low",
            code_sensitivity_descriptor="code",
            expected_damage=0.0,
            risk_budget_percent=0,
            affected_area="small",
        ),
        assessment_id="asm_1",
        repo_id="r",
        repo_root="/repo",
    )

    payload = composition.assess_payload(outcome)

    assert payload["applied_snapshot_provenance"] == {"snapshot_id": "snap_1"}
    assert "graph_refinement" not in payload


def test_assess_payload_exposes_prior_provenance() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.ASK_HUMAN,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={},
        repo_id="r",
        repo_root="/repo",
        provenance={"prior_provenance": {
            "source": "shipped",
            "sources": ["shipped"],
            "calibration_tags": ["population-v1"],
            "snapshot_ids": [],
            "targets": {},
        }},
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="Moderate", value_after_risk_band="Borderline",
            confidence_band="medium", confidence_percent=60,
            code_sensitivity_label="Moderate", code_sensitivity_descriptor="code",
            expected_damage=0.1, risk_budget_percent=50, affected_area="small",
        ),
        assessment_id="asm_1", repo_id="r", repo_root="/repo",
    )

    assert composition.assess_payload(outcome)["prior_provenance"]["source"] == "shipped"


def test_assess_payload_exposes_repo_state_and_graph_provenance() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.REJECT,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={
            "symbol_scope_evidence": {
                "symbol_fanin": {
                    "percentile": 0.95,
                    "caller_count": 12,
                    "resolution_method": "location",
                    "graph_freshness": "fresh",
                    "provider_version": "1.1.1",
                    "index_version": "24",
                    "fallback_reason": None,
                },
                "file_fanin_rollup": {
                    "percentile": 1.0,
                    "distinct_caller_count": 13,
                    "max_caller_count": 7,
                    "symbol_count": 5,
                    "resolution_method": "file_location",
                    "graph_freshness": "fresh",
                    "fallback_reason": None,
                },
            }
        },
        repo_id="r",
        repo_root="/repo",
        provenance={
            "provider": "pebra",
            "source_type": "derived",
            "graph_provenance": {
                "engine": "CodeGraph",
                "provider_version": "1.1.1",
                "index_version": "24",
            },
            "repo_state": {
                "repo_head_sha": "abc123",
                "worktree_dirty": True,
                "assessed_repo_root": "/repo",
            },
        },
        assessed_commit="abc123",
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="High",
            value_after_risk_band="Negative",
            confidence_band="low",
            confidence_percent=40,
            code_sensitivity_label="High",
            code_sensitivity_descriptor="code",
            expected_damage=0.2,
            risk_budget_percent=100,
            affected_area="file",
        ),
        assessment_id="asm_1",
        repo_id="r",
        repo_root="/repo",
    )

    payload = composition.assess_payload(outcome)

    assert payload["repo_state"] == {
        "repo_head_sha": "abc123",
        "worktree_dirty": True,
        "assessed_repo_root": "/repo",
    }
    assert payload["graph_provenance"]["engine"] == "CodeGraph"
    assert payload["graph_provenance"]["graph_freshness"] == "fresh"
    assert payload["graph_provenance"]["provider_version"] == "1.1.1"
    assert payload["graph_provenance"]["index_version"] == "24"
    assert payload["graph_provenance"]["symbol_fanin"]["caller_count"] == 12
    assert "provider_version" not in payload["graph_provenance"]["symbol_fanin"]
    assert payload["graph_provenance"]["file_fanin_rollup"]["distinct_caller_count"] == 13
    # multi-language honesty must reach the JSON/MCP consumer (defaults None when absent, as here)
    assert "structure_tier" in payload["graph_provenance"]
    assert "language_capability" in payload["graph_provenance"]


def test_assess_payload_forwards_language_capability_and_structure_tier() -> None:
    cap = {"language": "csharp", "tier": "partial", "signature_coverage_ratio": 0.0}
    result = AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={"symbol_scope_evidence": {"structure_tier": "codegraph_structural"}},
        repo_id="r",
        repo_root="/repo",
        provenance={"graph_provenance": {
            "engine": "CodeGraph", "provider_version": "1.1.1", "index_version": "24",
            "structure_tier": "codegraph_structural", "language_capability": cap,
        }},
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="Low", value_after_risk_band="Positive", confidence_band="high",
            confidence_percent=90, code_sensitivity_label="Low", code_sensitivity_descriptor="code",
            expected_damage=0.0, risk_budget_percent=100, affected_area="file"),
        assessment_id="asm_2", repo_id="r", repo_root="/repo",
    )
    gp = composition.assess_payload(outcome)["graph_provenance"]
    assert gp["structure_tier"] == "codegraph_structural"
    assert gp["language_capability"] == cap


def test_assess_payload_does_not_claim_graph_engine_without_graph_evidence() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={"symbol_scope_evidence": {}},
        repo_id="r",
        repo_root="/repo",
        provenance={"provider": "pebra", "source_type": "derived"},
        assessed_commit="abc123",
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="Low",
            value_after_risk_band="Positive",
            confidence_band="high",
            confidence_percent=90,
            code_sensitivity_label="Low",
            code_sensitivity_descriptor="code",
            expected_damage=0.0,
            risk_budget_percent=0,
            affected_area="small",
        ),
        assessment_id="asm_1",
        repo_id="r",
        repo_root="/repo",
    )

    payload = composition.assess_payload(outcome)

    assert payload["graph_provenance"]["engine"] is None
    assert payload["graph_provenance"]["graph_freshness"] == "unknown"


def test_ask_human_payload_exposes_bound_approval_request_without_self_authorizing_spec() -> None:
    explanation = Explanation(
        risk_level_band="High",
        value_after_risk_band="Positive",
        confidence_band="medium",
        confidence_percent=70,
        code_sensitivity_label="High",
        code_sensitivity_descriptor="public API",
        expected_damage=0.36,
        risk_budget_percent=180,
        affected_area="shared contract",
        why=["The proposed edit changes a high-impact public contract."],
    )
    candidate_binding = {
        "algorithm": "sha256-normalized-content-v1",
        "files": {"src/api.ts": "abc123"},
    }
    result = AssessmentResult(
        recommended_decision=Decision.ASK_HUMAN,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.ELEVATED_REVIEW,
        scores={
            "expected_loss": 0.36,
            "benefit": 0.50,
            "expected_utility": 0.14,
            "rau": 0.08,
            "symbol_scope_evidence": {},
        },
        repo_id="r",
        repo_root="/repo",
        decision_reason="Risk remains above the autonomous threshold after revision.",
        model_guidance_packet={
            "binding": {
                "candidate": candidate_binding,
                "required_controls": ["human_review", "targeted_tests"],
            },
            "advisory": {},
        },
    )
    action = CandidateAction(
        id="edit-api", label="edit api", action_type="modify",
        expected_files=["src/api.ts"],
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=explanation,
        assessment_id="asm_42",
        repo_id="r",
        repo_root="/repo",
        scored_actions=[ScoredAction(action=action, result=result, explanation=explanation)],
    )

    payload = composition.assess_payload(outcome)

    assert payload["decision_reason"] == result.decision_reason
    assert payload["next_action"] == {
        "type": "request_human_approval",
        "status": "pending",
        "assessment_id": "asm_42",
        "action_id": "edit-api",
        "candidate_binding": candidate_binding,
        "risk_benefit": {
            "expected_loss": 0.36,
            "benefit": 0.50,
            "expected_utility": 0.14,
            "rau": 0.08,
        },
        "reason": result.decision_reason,
        "required_controls": ["human_review", "targeted_tests"],
        "trusted_actor_required": True,
    }
    assert "sanction_spec" not in payload["next_action"]


def test_non_review_payload_does_not_claim_human_approval_is_required() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.REJECT,
        requires_confirmation=False,
        action_status=ActionStatus.REJECTED,
        risk_mode=RiskMode.NORMAL,
        scores={"symbol_scope_evidence": {}},
        repo_id="r",
        repo_root="/repo",
        decision_reason="Expected utility is negative.",
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="High", value_after_risk_band="Negative", confidence_band="high",
            confidence_percent=90, code_sensitivity_label="High",
            code_sensitivity_descriptor="code", expected_damage=0.5,
            risk_budget_percent=200, affected_area="shared code",
        ),
        assessment_id="asm_reject", repo_id="r", repo_root="/repo",
    )

    payload = composition.assess_payload(outcome)

    assert payload["next_action"] == {"type": "stop", "reason": result.decision_reason}
