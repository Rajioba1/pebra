from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.continuity import smoke


def _assessment(*, status: str = "available", facts: list[dict] | None = None) -> dict:
    return {
        "assessment_id": "asm_revision",
        "recommended_decision": "proceed",
        "scores": {
            "expected_loss": 0.12,
            "benefit": 0.5,
            "expected_utility": 0.38,
            "utility_sd": 0.08,
            "rau": 0.30,
            "effective_threshold": 0.20,
            "calibration_lanes": {
                "risk": {"predicted_expected_loss": 0.12},
                "benefit": {"predicted_benefit": 0.5},
                "context": {"language": "typescript", "language_tier": "full"},
            },
            "risk_probability_updates": [
                {
                    "fact_kind": "exported_binding_continuity",
                    "event": "public_api_break",
                    "risk_source": "graph_modify_risk",
                    "provider": "materialized_codegraph",
                    "owner_node_ids": ["owner-1"],
                    "original_probability": 0.4,
                    "revised_probability": 0.14,
                }
            ],
        },
        "graph_refinement": {
            "selected": True,
            "status": status,
            "evidence": {
                "facts": facts if facts is not None else [
                    {
                        "fact_kind": "exported_binding_continuity",
                        "event": "public_api_break",
                        "risk_source": "graph_modify_risk",
                        "owner_node_ids": ["owner-1"],
                    }
                ],
            },
        },
    }


def test_cases_are_deterministic_and_include_safe_and_adversarial_candidates(tmp_path: Path) -> None:
    harmful = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-old\n+new\n"
    safe = harmful + "@@ -3,0 +4 @@\n+export const old = new;\n"
    cases = smoke.candidate_cases(harmful, safe)

    assert [case.case_id for case in cases] == [
        "harmful_no_alias",
        "harmful_wrapper_decoy",
        "safe_const_alias",
        "safe_reexport_alias",
    ]
    assert len({case.patch_hash for case in cases}) == 4
    assert "=> {}" in cases[1].patch
    assert "export { new as old };" in cases[3].patch


def test_row_uses_provider_payload_not_case_expectation() -> None:
    case = smoke.SmokeCase("misleading_safe_name", "patch", consumer_should_pass=True)
    row = smoke.build_row(
        case=case,
        repo_sha="abc123",
        origin_assessment={"assessment_id": "asm_origin", "scores": {"expected_loss": 0.4}},
        revision_assessment=_assessment(status="unavailable", facts=[]),
        gate_result={"permission": "deny", "tier": "must_consult"},
        oracle=smoke.OracleResult(
            build_ran=True,
            build_passed=True,
            consumer_test_ran=True,
            consumer_test_passed=True,
            completion_test_ran=True,
            completion_test_passed=True,
        ),
    )

    assert row["proof_class"] == "proof_unavailable_consumer_passed"
    assert row["proof_fired"] is False
    assert row["provider_status"] == "unavailable"
    assert row["fixture_expected_consumer_result"] == "pass"


def test_denied_candidate_still_records_isolated_oracle_label() -> None:
    row = smoke.build_row(
        case=smoke.SmokeCase("harmful", "patch", consumer_should_pass=False),
        repo_sha="abc123",
        origin_assessment={"assessment_id": "asm_origin", "scores": {"expected_loss": 0.4}},
        revision_assessment=_assessment(),
        gate_result={"permission": "deny", "tier": "must_consult"},
        oracle=smoke.OracleResult(
            build_ran=True,
            build_passed=True,
            consumer_test_ran=True,
            consumer_test_passed=False,
            completion_test_ran=True,
            completion_test_passed=True,
        ),
    )

    assert row["gate_permission"] == "deny"
    assert row["label_scope"] == "isolated_candidate_oracle"
    assert row["candidate_applied_to_governed_repo"] is False
    assert row["harm_observed"] is True
    assert row["proof_class"] == "proof_fired_consumer_failed"


def test_write_rows_is_stable_jsonl(tmp_path: Path) -> None:
    output = tmp_path / "smoke.jsonl"
    rows = [{"case_id": "z", "value": 1}, {"case_id": "a", "value": 2}]

    smoke.write_rows(output, rows)

    parsed = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in parsed] == ["a", "z"]
    assert output.read_bytes().endswith(b"\n")


def test_each_case_uses_an_isolated_revision_store(tmp_path: Path) -> None:
    first = smoke.SmokeCase("first", "patch-a", consumer_should_pass=True)
    second = smoke.SmokeCase("second", "patch-b", consumer_should_pass=False)

    assert smoke.case_db_path(tmp_path, first) != smoke.case_db_path(tmp_path, second)


def test_partial_output_is_distinct_from_validated_artifact(tmp_path: Path) -> None:
    output = tmp_path / "smoke.jsonl"

    assert smoke.partial_output_path(output) == tmp_path / "smoke.partial.jsonl"


def test_graph_route_accepts_conservative_human_escalation_after_risk_reduction() -> None:
    row = {
        "proof_fired": True,
        "origin_expected_loss": 0.36,
        "predicted_expected_loss": 0.1494,
        "revision_decision": "ask_human",
        "revision_assessment_id": "asm_2",
        "gate_permission": "ask",
        "gate_matched_assessment_id": "asm_2",
    }

    assert smoke.graph_route_observed(row) is True
    assert smoke.graph_route_observed({**row, "gate_matched_assessment_id": "asm_stale"}) is False


def test_validate_rows_rejects_oracle_mismatch() -> None:
    rows = [{
        "case_id": "safe_const_alias",
        "fixture_expected_consumer_result": "pass",
        "consumer_test_ran": True,
        "consumer_test_passed": False,
        "build_ran": True,
        "build_passed": True,
        "provider_status": "available",
    }]

    with pytest.raises(RuntimeError, match="consumer oracle mismatch"):
        smoke.validate_rows(rows)
