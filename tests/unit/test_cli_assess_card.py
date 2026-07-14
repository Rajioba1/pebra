import json

import pytest

from pebra.cli import assess as assess_cli
from pebra.cli.assess import render_card
from pebra.cli.main import build_parser
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


def test_render_card_surfaces_prior_source_and_version() -> None:
    result = AssessmentResult(
        recommended_decision=Decision.ASK_HUMAN,
        requires_confirmation=True,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={},
        repo_id="r",
        repo_root="/repo",
        provenance={"prior_provenance": {
            "source": "shipped", "calibration_tags": ["population-v1"],
        }},
    )

    card = render_card(result, _explanation())

    assert "Prior Source:      Shipped (population-v1)" in card


def test_assess_parser_accepts_host_only_task_obligations_sidecar() -> None:
    args = build_parser().parse_args([
        "assess",
        "request.json",
        "--trusted-task-obligations-file",
        "obligations.json",
    ])

    assert args.trusted_task_obligations_file == "obligations.json"


def test_assess_parser_accepts_host_metadata_flag() -> None:
    args = build_parser().parse_args([
        "assess",
        "request.json",
        "--json",
        "--include-host-metadata",
    ])

    assert args.include_host_metadata is True


@pytest.mark.parametrize("payload", [None, {}, {"required_files": []}])
def test_trusted_obligations_sidecar_rejects_empty_payload(tmp_path, payload) -> None:
    path = tmp_path / "obligations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="obligations"):
        assess_cli._load_trusted_task_obligations(path)  # noqa: SLF001
