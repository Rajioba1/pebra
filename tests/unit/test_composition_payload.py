from pebra.app.assess_controller import AssessmentOutcome
from pebra import composition
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult


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
