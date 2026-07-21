from pebra.app.assess_controller import AssessmentOutcome, ScoredAction
from pebra import composition
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult, CandidateAction, CandidateGraphRiskEvidence


def test_assess_payload_hides_host_snapshot_provenance_by_default() -> None:
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

    assert "applied_snapshot_provenance" not in payload
    assert "prior_provenance" not in payload
    assert "graph_refinement" not in payload

    host_payload = composition.assess_payload(outcome, include_host_metadata=True)
    assert host_payload["applied_snapshot_provenance"] == {"snapshot_id": "snap_1"}
    assert "prior_provenance" in host_payload


def test_assess_payload_exposes_graph_failure_reason_without_inventing_scope() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.INSPECT_FIRST,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={},
        repo_id="r",
        repo_root="/repo",
        provenance={
            "graph_provenance": {
                "engine": "CodeGraph",
                "status": "unavailable",
                "fallback_reason": "codegraph sync failed",
            }
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

    provenance = composition.assess_payload(outcome)["graph_provenance"]

    assert provenance["engine"] == "CodeGraph"
    assert provenance["status"] == "unavailable"
    assert provenance["fallback_reason"] == "codegraph sync failed"
    assert provenance["graph_scope_digest"] is None


def test_assess_payload_exposes_prior_provenance_only_to_host() -> None:
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

    assert "prior_provenance" not in composition.assess_payload(outcome)
    payload = composition.assess_payload(outcome, include_host_metadata=True)
    assert payload["prior_provenance"]["source"] == "shipped"


def test_assess_payload_exposes_graph_refinement_only_to_host() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.ASK_HUMAN,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.ELEVATED_REVIEW,
        scores={},
        repo_id="r",
        repo_root="/repo",
    )
    explanation = Explanation(
        risk_level_band="Moderate", value_after_risk_band="Borderline",
        confidence_band="medium", confidence_percent=60,
        code_sensitivity_label="Moderate", code_sensitivity_descriptor="code",
        expected_damage=0.1, risk_budget_percent=50, affected_area="small",
    )
    scored = ScoredAction(
        action=CandidateAction(
            id="a1", label="rename", action_type="edit", expected_files=["api.ts"]
        ),
        result=result,
        explanation=explanation,
        candidate_graph_risk_evidence=CandidateGraphRiskEvidence(
            status="available",
            provider="materialized_codegraph",
            language="typescript",
            witness="ecmascript",
            witness_version="1",
            engine_version="1.1.1",
        ),
        refinement_enabled=True,
        refinement_selected=True,
        refinement_status="available",
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=explanation,
        assessment_id="asm_1",
        repo_id="r",
        repo_root="/repo",
        scored_actions=[scored],
    )

    assert "graph_refinement" not in composition.assess_payload(outcome)
    host_payload = composition.assess_payload(outcome, include_host_metadata=True)
    assert host_payload["graph_refinement"]["evidence"]["witness"] == "ecmascript"


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
        candidate_replay={"status": "available"},
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
        "command": "pebra accept-risk --apply",
    }
    assert "sanction_spec" not in payload["next_action"]


def test_proceed_payload_names_exact_dynamic_apply_command() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={"symbol_scope_evidence": {}},
        repo_id="r",
        repo_root="/repo",
        decision_reason="Candidate is authorized.",
    )
    outcome = AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=Explanation(
            risk_level_band="Low", value_after_risk_band="Positive", confidence_band="high",
            confidence_percent=90, code_sensitivity_label="Low",
            code_sensitivity_descriptor="code", expected_damage=0.01,
            risk_budget_percent=5, affected_area="local",
        ),
        assessment_id="asm_99", repo_id="r", repo_root="/repo",
        candidate_replay={"status": "available"},
    )

    assert composition.assess_payload(outcome)["next_action"] == {
        "type": "apply_exact_candidate_then_verify",
        "reason": "Candidate is authorized.",
        "assessment_id": "asm_99",
        "command": "pebra apply-candidate --assessment-id asm_99",
    }


def test_candidate_commands_are_not_advertised_without_replay_data() -> None:
    explanation = Explanation(
        risk_level_band="Moderate", value_after_risk_band="Borderline",
        confidence_band="medium", confidence_percent=60,
        code_sensitivity_label="Moderate", code_sensitivity_descriptor="code",
        expected_damage=0.1, risk_budget_percent=50, affected_area="small",
    )

    def next_action(decision: Decision) -> dict:
        result = AssessmentResult(
            recommended_decision=decision,
            requires_confirmation=decision is Decision.ASK_HUMAN,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"symbol_scope_evidence": {}},
            repo_id="r", repo_root="/repo", decision_reason="reason",
        )
        outcome = AssessmentOutcome(
            recommended_result=result, recommended_explanation=explanation,
            assessment_id="asm_legacy", repo_id="r", repo_root="/repo",
        )
        return composition.assess_payload(outcome)["next_action"]

    ask_action = next_action(Decision.ASK_HUMAN)
    proceed_action = next_action(Decision.PROCEED)

    assert ask_action["type"] == "request_human_approval"
    assert proceed_action["type"] == "apply_exact_candidate_then_verify"
    assert "command" not in ask_action
    assert "command" not in proceed_action
    assert "assessment_id" not in proceed_action


def test_unscoped_reject_payload_requests_review_without_claiming_override() -> None:
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

    action = payload["next_action"]
    assert action["type"] == "request_human_review"
    assert action["reason"] == result.decision_reason
    assert action["override"]["available"] is False
    assert "command" not in action["override"]


def _reject_outcome(*, gate: int, scores: dict, replay: bool = True) -> AssessmentOutcome:
    explanation = Explanation(
        risk_level_band="High", value_after_risk_band="Negative", confidence_band="high",
        confidence_percent=90, code_sensitivity_label="High",
        code_sensitivity_descriptor="shared code", expected_damage=0.73,
        risk_budget_percent=200, affected_area="shared code",
    )
    candidate_binding = {
        "algorithm": "sha256-normalized-content-v1",
        "files": {"src/api.ts": "a" * 64},
    }
    result = AssessmentResult(
        recommended_decision=Decision.REJECT,
        requires_confirmation=False,
        action_status=ActionStatus.REJECTED,
        risk_mode=RiskMode.NORMAL,
        scores={**scores, "symbol_scope_evidence": {}},
        repo_id="r",
        repo_root="/repo",
        gates_fired=[{
            "gate": gate,
            "name": {
                3: "expected_loss_over_threshold",
                4: "negative_rau",
                9: "revision_has_no_credible_benefit",
            }.get(gate, "policy_violation"),
            **(
                {"expected_loss": 0.73, "threshold": 0.5} if gate == 3
                else {"rau": -0.61} if gate == 4
                else {"benefit": 0.0} if gate == 9
                else {}
            ),
        }],
        decision_reason="This candidate has negative risk-adjusted utility.",
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
    return AssessmentOutcome(
        recommended_result=result,
        recommended_explanation=explanation,
        assessment_id="asm_42",
        repo_id="r",
        repo_root="/repo",
        scored_actions=[ScoredAction(action=action, result=result, explanation=explanation)],
        candidate_replay={"status": "available"} if replay else {},
    )


def test_risk_reject_payload_offers_only_bound_interactive_human_override() -> None:
    payload = composition.assess_payload(_reject_outcome(
        gate=3,
        scores={
            "expected_loss": 0.73,
            "benefit": 0.20,
            "expected_utility": -0.53,
            "rau": -0.61,
        },
    ))

    assert payload["next_action"] == {
        "type": "request_human_approval",
        "origin_decision": "reject",
        "status": "pending",
        "assessment_id": "asm_42",
        "action_id": "edit-api",
        "candidate_binding": {
            "algorithm": "sha256-normalized-content-v1",
            "files": {"src/api.ts": "a" * 64},
        },
        "risk_benefit": {
            "expected_loss": 0.73,
            "benefit": 0.20,
            "expected_utility": -0.53,
            "rau": -0.61,
        },
        "reason": "This candidate has negative risk-adjusted utility.",
        "controlling_gate": 3,
        "required_controls": ["human_review", "targeted_tests"],
        "trusted_actor_required": True,
        "override": {
            "available": True,
            "command": "pebra accept-risk --apply --assessment-id asm_42",
        },
    }


def test_reject_payload_with_malformed_candidate_binding_never_advertises_override() -> None:
    outcome = _reject_outcome(
        gate=3,
        scores={
            "expected_loss": 0.73,
            "benefit": 0.20,
            "expected_utility": -0.53,
            "rau": -0.61,
        },
    )
    outcome.recommended_result.model_guidance_packet["binding"]["candidate"] = {}

    action = composition.assess_payload(outcome)["next_action"]

    assert action["type"] == "request_human_review"
    assert action["override"]["available"] is False
    assert "command" not in action["override"]


def test_policy_reject_payload_requires_human_route_without_risk_override() -> None:
    payload = composition.assess_payload(_reject_outcome(
        gate=1,
        scores={
            "expected_loss": 0.73,
            "benefit": 0.20,
            "expected_utility": -0.53,
            "rau": -0.61,
        },
    ))

    action = payload["next_action"]
    assert action["type"] == "request_human_review"
    assert action["origin_decision"] == "reject"
    assert action["controlling_gate"] == 1
    assert action["override"] == {
        "available": False,
        "unavailable_reason": (
            "This rejection is not eligible for generic risk acceptance; revise the candidate or "
            "follow a maintainer-authored policy change, then reassess."
        ),
    }
    assert "command" not in action["override"]


def test_reject_payload_with_untrusted_scores_never_advertises_override() -> None:
    payload = composition.assess_payload(_reject_outcome(
        gate=3,
        scores={
            "expected_loss": float("nan"),
            "benefit": 0.20,
            "expected_utility": -0.53,
            "rau": -0.61,
        },
    ))

    action = payload["next_action"]
    assert action["type"] == "request_human_review"
    assert action["risk_benefit"] is None
    assert action["override"] == {
        "available": False,
        "unavailable_reason": (
            "Risk-benefit evidence is unavailable or malformed; reassess before human review."
        ),
    }
