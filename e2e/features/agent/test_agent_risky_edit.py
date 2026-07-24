"""Phase E3: one full agent cycle over the CLI boundary — assess -> edit -> verify -> record -> learn.

Proves a scripted agent can use PEBRA on real code (get the decision + math, act within the approved
envelope, record the outcome, trigger learning) WITHOUT importing pebra internals. The boundary is
enforced by test_boundary_discipline.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from e2e.utils import agent_harness as ah
from e2e.utils import cli_harness as ch

_DECISIONS = {
    "proceed", "inspect_first", "test_first", "revise_safer", "ask_human", "reject",
}


def test_agent_completes_full_cycle_over_cli_boundary(risky_repo, e2e_db, request_json_path):
    transcript = ah.run_pre_edit_cycle(risky_repo, e2e_db, request_json_path, actual_success=True)

    payload = transcript.payload
    assert payload["recommended_decision"] in _DECISIONS
    assert payload["recommended_decision"] == "proceed", json.dumps(payload, sort_keys=True)
    assert transcript.verify_passed is True  # verify PROCEEDed within the approved envelope
    # PEBRA returned real math + guidance to the agent:
    assert 0.0 <= payload["scores"]["rau"] <= 1.0
    assert isinstance(
        payload["model_guidance_packet"]["binding"]["required_checks_before_commit"], list
    )
    # learning measured the prediction (observed or censored):
    assert transcript.learn_result["observed"] + transcript.learn_result["censored"] >= 1


def test_medium_confidence_candidate_requires_inspection_over_cli_boundary(
    risky_repo, e2e_db, request_json_path, tmp_path
):
    request = json.loads(request_json_path.read_text(encoding="utf-8"))
    request["evidence"]["p_success"] = 0.70
    request["evidence"]["edit_confidence_factors"] = {
        "p_success": 0.70,
        "evidence_quality": 0.72,
        "testability": 0.70,
        "reversibility": 0.80,
        "source_reliability": 0.78,
        "scope_control": 0.80,
    }
    path = tmp_path / "medium-confidence-request.json"
    path.write_text(json.dumps(request), encoding="utf-8")

    assessed = ch.assess(path, repo_root=risky_repo, db=e2e_db)

    assert assessed["recommended_decision"] == "inspect_first"
    assert any(
        gate["name"] == "medium_edit_confidence"
        for gate in assessed["gates_fired"]
    )


def _request_with_patch(request_json_path, tmp_path, patch, expected_file):
    request = json.loads(request_json_path.read_text(encoding="utf-8"))
    action = request["candidate_actions"][0]
    action["proposed_patch"] = patch
    action["expected_files"] = [expected_file]
    action["affected_symbols"] = [f"{expected_file}::value"]
    request["evidence"]["symbol_diff"]["changed_symbols"] = [f"{expected_file}::value"]
    path = tmp_path / "request-with-candidate.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _completed_checks(payload):
    checks = payload["model_guidance_packet"]["binding"]["required_checks_before_commit"]
    return {str(check): "passed" for check in checks}


def test_protocol_v4_runtime_applies_stages_verifies_and_records_exact_candidate(
    risky_repo, request_json_path, tmp_path
):
    db = risky_repo / ".pebra" / "pebra.db"
    candidate = ch.candidate_patch(
        [{
            "path": "auth_service.py",
            "old_string": "return token == secret  # a constant-time compare would be safer",
            "new_string": "return token == secret  # verified exact candidate",
        }],
        repo_root=risky_repo,
    )
    request = _request_with_patch(
        request_json_path, tmp_path, candidate["proposed_patch"], "auth_service.py"
    )
    assessed = ch.assess(request, repo_root=risky_repo, db=db)
    assert assessed["recommended_decision"] == "proceed", json.dumps(assessed, sort_keys=True)
    applied = ch.apply_candidate(assessed["assessment_id"], repo_root=risky_repo, db=db)
    ah.stage_exact_changed_files(risky_repo, applied["changed_files"])
    assert ah.staged_files(risky_repo) == tuple(applied["changed_files"])

    passed, verified = ch.verify(
        assessed["assessment_id"], repo_root=risky_repo, db=db,
        completed_checks=_completed_checks(assessed), scope="staged",
    )
    assert passed is True, json.dumps(verified, sort_keys=True)
    assert verified["pre_commit_decision"] == "proceed"
    ch.record_outcome(
        assessed["assessment_id"], "completed", repo_root=risky_repo, db=db,
        detail={"actual_success": True, "lesson": "Keep token validation changes exact."},
    )
    recalled = ch.explore(
        "token validation", files=("auth_service.py",), repo_root=risky_repo
    )
    assert recalled["learning_context"]["status"] == "available"
    assert recalled["learning_context"]["entries"][0]["assessment_id"] == assessed["assessment_id"]


def test_sensitive_proceed_requires_human_confirmation_before_exact_apply(
    risky_repo, request_json_path, tmp_path
):
    db = risky_repo / ".pebra" / "pebra.db"
    candidate = ch.candidate_patch(
        [{
            "path": "auth_service.py",
            "old_string": "return token == secret  # a constant-time compare would be safer",
            "new_string": "return token == secret  # candidate still needs human confirmation",
        }],
        repo_root=risky_repo,
    )
    request = json.loads(request_json_path.read_text(encoding="utf-8"))
    request["evidence"]["criticality_stage"] = "C3"
    request["evidence"]["criticality_value"] = 0.80
    action = request["candidate_actions"][0]
    action["proposed_patch"] = candidate["proposed_patch"]
    action["expected_files"] = ["auth_service.py"]
    path = tmp_path / "confirmation-required-request.json"
    path.write_text(json.dumps(request), encoding="utf-8")

    assessed = ch.assess(path, repo_root=risky_repo, db=db)

    assert assessed["recommended_decision"] == "proceed"
    assert assessed["requires_confirmation"] is True
    with pytest.raises(ch.CLIError, match="did not authorize candidate application"):
        ch.apply_candidate(assessed["assessment_id"], repo_root=risky_repo, db=db)


def test_literal_pathspec_stages_returned_bracket_filename_only(
    risky_repo, request_json_path, tmp_path
):
    literal = "[ab].py"
    wildcard_match = "a.py"
    (risky_repo / literal).write_text("value = 1\n", encoding="utf-8")
    (risky_repo / wildcard_match).write_text("value = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--all"], cwd=risky_repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-qm", "metacharacter fixture"], cwd=risky_repo,
        check=True, capture_output=True, text=True,
    )
    db = risky_repo / ".pebra" / "pebra.db"
    candidate = ch.candidate_patch(
        [{"path": literal, "old_string": "value = 1\n", "new_string": "value = 2\n"}],
        repo_root=risky_repo,
    )
    request = _request_with_patch(
        request_json_path, tmp_path, candidate["proposed_patch"], literal
    )
    assessed = ch.assess(request, repo_root=risky_repo, db=db)
    assert assessed["recommended_decision"] == "proceed", json.dumps(assessed, sort_keys=True)
    applied = ch.apply_candidate(assessed["assessment_id"], repo_root=risky_repo, db=db)
    assert applied["changed_files"] == [literal]
    (risky_repo / wildcard_match).write_text("value = 99\n", encoding="utf-8")
    ah.stage_exact_changed_files(risky_repo, applied["changed_files"])

    assert ah.staged_files(risky_repo) == (literal,)
    unstaged = subprocess.run(
        ["git", "diff", "--name-only", "--", wildcard_match], cwd=risky_repo,
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert unstaged == [wildcard_match]
    passed, verified = ch.verify(
        assessed["assessment_id"], repo_root=risky_repo, db=db,
        completed_checks=_completed_checks(assessed), scope="staged",
    )
    assert passed is True, json.dumps(verified, sort_keys=True)
    assert verified["pre_commit_decision"] == "proceed"
    ch.record_outcome(
        assessed["assessment_id"], "completed", repo_root=risky_repo, db=db,
        detail={"actual_success": True, "lesson": "Treat bracket filenames as literal paths."},
    )
    recalled = ch.explore(
        "Harden bearer-token validation", files=(literal,), repo_root=risky_repo
    )
    assert recalled["learning_context"]["status"] == "available"
    assert any(
        entry["assessment_id"] == assessed["assessment_id"]
        for entry in recalled["learning_context"]["entries"]
    )
    assert literal in recalled["learning_context"]["entries"][0]["target_files"]
