from pebra.cli.assess import render_card
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult


def _explanation() -> Explanation:
    return Explanation(
        risk_level_band="High",
        value_after_risk_band="Negative",
        confidence_band="low",
        confidence_percent=42,
        code_sensitivity_label="High",
        code_sensitivity_descriptor="sensitive code",
        expected_damage=0.31,
        risk_budget_percent=140,
        affected_area="small",
        why=["delete touches a high fan-in file"],
    )


def test_render_card_surfaces_graph_rollup_in_human_labels() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.REJECT,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={},
        repo_id="r",
        repo_root="/repo",
        symbol_scope_evidence={
            "file_operation_kind": "DELETE",
            "file_fanin_rollup": {
                "percentile": 1.0,
                "distinct_caller_count": 13,
                "resolution_method": "file_location",
                "graph_freshness": "fresh",
            },
        },
    )

    card = render_card(result, _explanation())

    assert "Graph Evidence:" in card
    assert "Graph engine: CodeGraph" in card
    assert "Graph freshness: fresh" in card
    assert "Changed operation: delete file" in card
    assert "File fan-in rollup: 1.000 percentile" in card
    assert "Graph callers/references: 13" in card
