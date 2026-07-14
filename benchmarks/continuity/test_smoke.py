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
    assert [case.calibration_fit_eligible for case in cases] == [True, True, True, True]
    assert all(case.origin_patch == harmful for case in cases)


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


def test_action_success_requires_build_consumer_and_completion() -> None:
    row = smoke.build_row(
        case=smoke.SmokeCase("incomplete", "patch", consumer_should_pass=True),
        repo_sha="abc123",
        origin_assessment={"assessment_id": "asm_origin", "scores": {"expected_loss": 0.4}},
        revision_assessment=_assessment(),
        gate_result={"permission": "allow", "tier": "pass"},
        oracle=smoke.OracleResult(
            build_ran=True,
            build_passed=True,
            consumer_test_ran=True,
            consumer_test_passed=True,
            completion_test_ran=True,
            completion_test_passed=False,
        ),
    )

    assert row["action_success"] is False


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


def test_assess_requests_host_only_refinement_metadata(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(smoke.advisory_check_real, "_build_request", lambda *_a, **_k: {
        "candidate_actions": [{"id": "a"}], "thresholds": {},
    })

    def _assess(_request_path, **kwargs):
        seen.update(kwargs)
        seen["request"] = json.loads(Path(_request_path).read_text(encoding="utf-8"))
        return {"recommended_decision": "ask_human"}

    monkeypatch.setattr(smoke.cli_harness, "assess", _assess)

    smoke._assess(
        repo=tmp_path,
        db=tmp_path / "pebra.db",
        case=smoke.SmokeCase("case", "patch", consumer_should_pass=True),
        patch="patch",
        attempt=1,
    )

    assert seen["include_host_metadata"] is True
    request = seen["request"]
    assert request["thresholds"]["max_expected_loss_without_human"] == 0.01
    assert request["thresholds"]["c3_max_expected_loss_without_human"] == 0.01


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


def test_harmful_oracle_may_fail_build_or_completion_without_invalidating_label() -> None:
    row = {
        "case_id": "harmful_owner",
        "fixture_expected_consumer_result": "fail",
        "consumer_test_ran": True,
        "consumer_test_passed": False,
        "completion_test_ran": True,
        "completion_test_passed": False,
        "build_ran": True,
        "build_passed": False,
    }

    smoke.validate_oracle_row(row)


def test_safe_oracle_requires_clean_build_and_completion() -> None:
    row = {
        "case_id": "safe_owner",
        "fixture_expected_consumer_result": "pass",
        "consumer_test_ran": True,
        "consumer_test_passed": True,
        "completion_test_ran": True,
        "completion_test_passed": False,
        "build_ran": True,
        "build_passed": True,
    }

    with pytest.raises(RuntimeError, match="did not complete"):
        smoke.validate_oracle_row(row)


def test_calibration_owner_specs_are_independent_and_pinned() -> None:
    owners = smoke.calibration_owner_specs()

    assert len(owners) >= 2
    assert len({owner.cluster_id for owner in owners}) == len(owners)
    assert len({(owner.relative_path, owner.old_name) for owner in owners}) == len(owners)
    assert all(owner.old_name != owner.new_name for owner in owners)
    assert all(owner.measured_fanin > 0 for owner in owners)


def test_owner_patch_variants_change_only_the_exported_declaration(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "packages" / "zod" / "src" / "v3" / "api.ts"
    consumer = repo / "packages" / "zod" / "src" / "v3" / "consumer.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export function oldName() { return 1; }\n", encoding="utf-8")
    consumer.write_text("import { oldName } from './api';\nvoid oldName();\n", encoding="utf-8")
    smoke._git("init", cwd=repo)
    smoke._git("config", "user.email", "benchmark@example.invalid", cwd=repo)
    smoke._git("config", "user.name", "PEBRA Benchmark", cwd=repo)
    smoke._git("add", ".", cwd=repo)
    smoke._git("commit", "-m", "fixture", cwd=repo)
    owner = smoke.OwnerSpec(
        cluster_id="api-old-name",
        relative_path="packages/zod/src/v3/api.ts",
        old_name="oldName",
        new_name="newName",
        consumer_import="./api",
        measured_fanin=10,
        test_directory="packages/zod/src/v3/tests",
    )

    harmful, safe = smoke.owner_patch_variants(repo, owner)

    assert smoke.patch_applies(repo, harmful) is True
    assert smoke.patch_applies(repo, safe) is True
    assert "+export function newName()" in harmful
    assert "consumer.ts" not in harmful
    assert "void oldName();" not in harmful
    assert "export const oldName = newName;" not in harmful
    assert "+export const oldName = newName;" in safe
    assert "consumer.ts" not in safe
    assert harmful != safe
