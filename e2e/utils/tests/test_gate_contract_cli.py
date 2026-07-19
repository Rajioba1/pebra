"""Process-boundary acceptance tests for the versioned pre-edit gate envelope."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from e2e.utils import cli_harness


_MISSING = object()
_BINDING_ALGORITHM = "sha256-normalized-content-v1"
_REPLAY_ALGORITHM = "sha256-candidate-replay-v1"
_VALID_REPLAY = {
    "status": "available",
    "algorithm": _REPLAY_ALGORITHM,
    "digest": "a" * 64,
}
_SCORES = {"expected_loss": 0.75, "benefit": 0.2, "rau": -0.55}
_ENVELOPE_KEYS = {
    "schema_version",
    "permission",
    "tier",
    "reason",
    "warn",
    "risk_summary",
    "matched_assessment_id",
}
_FORBIDDEN_BLINDING_TERMS = (
    "pebra",
    "codegraph",
    "experiment",
    "oracle",
    "permission denied",
    "goal rejected",
)
_VALID_GATE = {
    "schema_version": 1,
    "permission": "allow",
    "tier": "pass",
    "reason": None,
    "warn": None,
    "risk_summary": None,
    "matched_assessment_id": None,
}


@dataclass(frozen=True)
class GateCase:
    repo: Path
    db: Path
    claude_event: dict
    codex_event: dict
    mismatch_event: dict


def _run(command: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _digest(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(b"\x01text\x00" + normalized.encode("utf-8")).hexdigest()


def _repo_id(repo: Path) -> str:
    return "repo_" + hashlib.sha1(str(repo.resolve()).encode("utf-8")).hexdigest()[:12]


def _seed_case(
    root: Path,
    *,
    decision: str,
    scores: object = _SCORES,
    replay: object = _MISSING,
) -> GateCase:
    repo = root / "repo"
    repo.mkdir(parents=True)
    target = repo / "target.txt"
    target.write_text("original\n", encoding="utf-8")
    graph = repo / ".pebra" / "import_graph.json"
    graph.parent.mkdir()
    graph.write_text(
        json.dumps({"god_node_scores": {"target.txt": 1.0}}),
        encoding="utf-8",
    )
    _run(["git", "init", "-q"], cwd=repo)
    _run(["git", "config", "user.name", "PEBRA E2E"], cwd=repo)
    _run(["git", "config", "user.email", "e2e@invalid.example"], cwd=repo)
    _run(["git", "add", "target.txt"], cwd=repo)
    _run(["git", "commit", "-qm", "fixture"], cwd=repo)
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo)

    content: dict = {
        "assessed_commit": head,
        "model_guidance_packet": {
            "binding": {
                "safe_scope": {"files": ["target.txt"]},
                "candidate": {
                    "algorithm": _BINDING_ALGORITHM,
                    "files": {"target.txt": _digest("changed\n")},
                },
            }
        },
        "scores": scores,
    }
    if replay is not _MISSING:
        content["request"] = {"candidate_replay": replay}

    db = root / "pebra.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE assessments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "repo_id TEXT, decision TEXT, content_json TEXT)"
        )
        connection.execute(
            "INSERT INTO assessments (repo_id, decision, content_json) VALUES (?, ?, ?)",
            (_repo_id(repo), decision, json.dumps(content)),
        )

    patch = (
        "*** Begin Patch\n"
        "*** Update File: target.txt\n"
        "@@\n"
        "-original\n"
        "+changed\n"
        "*** End Patch\n"
    )
    return GateCase(
        repo=repo,
        db=db,
        claude_event={
            "tool_name": "Write",
            "tool_input": {"file_path": "target.txt", "content": "changed\n"},
            "cwd": str(repo),
        },
        codex_event={
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "cwd": str(repo),
        },
        mismatch_event={
            "tool_name": "Write",
            "tool_input": {"file_path": "target.txt", "content": "changed!\n"},
            "cwd": str(repo),
        },
    )


@pytest.fixture
def gate_case(tmp_path) -> Callable[..., GateCase]:
    sequence = 0

    def create(**kwargs: object) -> GateCase:
        nonlocal sequence
        sequence += 1
        return _seed_case(tmp_path / f"case-{sequence}", **kwargs)

    return create


def _gate_hook(event: dict, *, db: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "pebra", "gate-hook", "--db", str(db)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _assert_candidate_hold(payload: dict) -> str:
    assert set(payload) == _ENVELOPE_KEYS
    assert payload["schema_version"] == 1
    assert payload["permission"] == "deny"
    assert payload["warn"] is None
    reason = payload["reason"]
    assert isinstance(reason, str)
    assert "exact candidate is held" in reason.lower()
    return reason


def test_gate_check_real_cli_emits_schema_one_envelope(tmp_path):
    payload = cli_harness.gate_check({}, db=tmp_path / "missing.db", consult_only=True)

    assert payload == {
        "schema_version": 1,
        "permission": "allow",
        "tier": "pass",
        "reason": None,
        "warn": None,
        "risk_summary": None,
        "matched_assessment_id": None,
    }


def test_gate_check_parse_limit_failure_is_fatal_contract(monkeypatch, tmp_path):
    stdout = (
        '{"schema_version":1,"permission":"allow","tier":"consulted",'
        '"reason":null,"warn":null,"risk_summary":{"decision":"proceed",'
        '"expected_loss":' + "9" * 5000 + ',"benefit":0.34,"rau":-0.27},'
        '"matched_assessment_id":"asm_1"}'
    )
    monkeypatch.setattr(
        cli_harness.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        cli_harness.gate_check({}, db=tmp_path / "pebra.db", consult_only=True)


@pytest.mark.parametrize("stdout", ("", "not-json"), ids=("empty", "invalid"))
def test_gate_check_conventional_parse_failure_is_fatal_contract(
    monkeypatch, tmp_path, stdout,
):
    monkeypatch.setattr(
        cli_harness.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        cli_harness.gate_check({}, db=tmp_path / "pebra.db", consult_only=True)


@pytest.mark.parametrize(
    "payload",
    (
        [],
        {},
        {**_VALID_GATE, "schema_version": 2},
        {**_VALID_GATE, "permission": "continue"},
        {**_VALID_GATE, "permission": []},
        {**_VALID_GATE, "tier": "unknown"},
        {**_VALID_GATE, "tier": []},
        {**_VALID_GATE, "permission": "allow", "tier": "must_consult"},
        {**_VALID_GATE, "permission": "deny", "tier": "positive_control"},
        {**_VALID_GATE, "permission": "deny", "tier": "must_consult", "reason": None},
        {**_VALID_GATE, "permission": "ask", "tier": "consulted_review", "reason": " "},
        {**_VALID_GATE, "reason": 7},
        {**_VALID_GATE, "warn": []},
        {**_VALID_GATE, "risk_summary": []},
        {**_VALID_GATE, "risk_summary": {
            "decision": "revise_safer", "expected_loss": float("nan"),
            "benefit": 0.34, "rau": -0.27,
        }},
        {**_VALID_GATE, "risk_summary": {
            "decision": "unknown", "expected_loss": 0.61,
            "benefit": 0.34, "rau": -0.27,
        }},
        {**_VALID_GATE, "risk_summary": {
            "decision": [], "expected_loss": 0.61,
            "benefit": 0.34, "rau": -0.27,
        }},
        {**_VALID_GATE, "risk_summary": {
            "decision": "proceed", "expected_loss": 10**1000,
            "benefit": 0.34, "rau": -0.27,
        }, "tier": "consulted", "matched_assessment_id": "asm_1"},
        *(
            {**_VALID_GATE, "matched_assessment_id": value}
            for value in ("", "asm_0", "asm_-1", "asm_exact", "garbage")
        ),
        {**_VALID_GATE, "tier": "consulted", "risk_summary": {
            "decision": "reject", "expected_loss": 0.61,
            "benefit": 0.34, "rau": -0.27,
        }, "matched_assessment_id": "asm_exact"},
        {key: value for key, value in _VALID_GATE.items() if key != "matched_assessment_id"},
    ),
)
def test_gate_envelope_rejects_unsupported_or_malformed_payload(payload):
    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"])


@pytest.mark.parametrize(
    ("permission", "tier", "decision"),
    (
        ("allow", "consulted", "proceed"),
        ("deny", "consulted_revise", "revise_safer"),
        ("deny", "consulted_prerequisite", "inspect_first"),
        ("deny", "consulted_prerequisite", "test_first"),
        ("ask", "consulted_review", "ask_human"),
        ("deny", "consulted_review", "reject"),
        ("deny", "consulted_review_unavailable", "ask_human"),
    ),
)
def test_gate_envelope_accepts_declared_risk_decision_matrix(permission, tier, decision):
    payload = {
        **_VALID_GATE,
        "permission": permission,
        "tier": tier,
        "reason": "Candidate requires a host decision.",
        "risk_summary": {
            "decision": decision,
            "expected_loss": 0.61,
            "benefit": 0.34,
            "rau": -0.27,
        },
        "matched_assessment_id": "asm_1",
    }

    assert cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"]) is payload


@pytest.mark.parametrize(
    ("permission", "tier", "decision"),
    (
        ("allow", "consulted", "reject"),
        ("deny", "consulted_revise", "proceed"),
        ("deny", "consulted_prerequisite", "ask_human"),
        ("ask", "consulted_review", "reject"),
        ("deny", "consulted_review", "ask_human"),
        ("deny", "consulted_review_unavailable", "reject"),
    ),
)
def test_gate_envelope_rejects_undeclared_risk_decision_matrix(permission, tier, decision):
    payload = {
        **_VALID_GATE,
        "permission": permission,
        "tier": tier,
        "reason": "Candidate requires a host decision.",
        "risk_summary": {
            "decision": decision,
            "expected_loss": 0.61,
            "benefit": 0.34,
            "rau": -0.27,
        },
        "matched_assessment_id": "asm_1",
    }

    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"])


def test_gate_envelope_allows_unknown_schema_one_top_level_fields():
    payload = {**_VALID_GATE, "future_host_metadata": {"opaque": True}}

    assert cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"]) is payload


def test_gate_hook_capabilities_emit_candidate_binding_protocol():
    result = subprocess.run(
        [sys.executable, "-m", "pebra", "gate-hook", "--capabilities"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["candidate_binding_protocol"] == _BINDING_ALGORITHM


def test_consult_only_holds_an_exact_restrictive_candidate_with_blinded_evidence(gate_case):
    case = gate_case(decision="ask_human", replay=_VALID_REPLAY)
    payload = cli_harness.gate_check(case.claude_event, db=case.db, consult_only=True)

    reason = _assert_candidate_hold(payload)
    assert payload["tier"] == "consulted_review_unavailable"
    assert payload["matched_assessment_id"] == "asm_1"
    assert payload["risk_summary"] == {"decision": "ask_human", **_SCORES}
    assert "reassess this candidate" in reason.lower()
    assert "pebra accept-risk --apply" not in reason
    lowered = reason.lower()
    assert all(term not in lowered for term in _FORBIDDEN_BLINDING_TERMS)


def test_interactive_gate_asks_only_for_replay_available_ask_human(gate_case):
    review = gate_case(decision="ask_human", replay=_VALID_REPLAY)
    payload = cli_harness.gate_check(review.claude_event, db=review.db)

    assert set(payload) == _ENVELOPE_KEYS
    assert payload["schema_version"] == 1
    assert payload["permission"] == "ask"
    assert payload["tier"] == "consulted_review"
    assert payload["risk_summary"] == {"decision": "ask_human", **_SCORES}
    assert payload["matched_assessment_id"] == "asm_1"
    assert "exact candidate is held" in payload["reason"].lower()
    assert "pebra accept-risk --apply" in payload["reason"]


def test_reject_returns_candidate_and_requires_a_different_route(gate_case):
    rejected = gate_case(decision="reject", replay=_VALID_REPLAY)
    payload = cli_harness.gate_check(rejected.claude_event, db=rejected.db)

    reason = _assert_candidate_hold(payload)
    assert payload["tier"] == "consulted_review"
    assert payload["risk_summary"] == {"decision": "reject", **_SCORES}
    assert "different candidate or route" in reason.lower()
    assert "accept-risk" not in reason.lower()


@pytest.mark.parametrize("event_name", ("claude_event", "codex_event"))
def test_installed_hooks_project_bound_review_to_blocking_deny(gate_case, event_name):
    review = gate_case(decision="ask_human", replay=_VALID_REPLAY)
    payload = _gate_hook(getattr(review, event_name), db=review.db)["hookSpecificOutput"]

    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"
    assert "exact candidate is held" in payload["permissionDecisionReason"].lower()
    assert "pebra accept-risk --apply" in payload["permissionDecisionReason"]
    assert "ask" not in payload.values()


@pytest.mark.parametrize(
    "replay",
    (
        _MISSING,
        "malformed",
        {"status": "unavailable"},
        {"status": "available"},
        {
            "status": "available",
            "algorithm": "sha256-candidate-replay-v0",
            "digest": "a" * 64,
        },
        {
            "status": "available",
            "algorithm": _REPLAY_ALGORITHM,
            "digest": "bad",
        },
    ),
    ids=(
        "missing",
        "malformed",
        "unavailable",
        "missing-algorithm-and-digest",
        "wrong-algorithm",
        "bad-digest",
    ),
)
def test_unavailable_replay_stays_blocking_without_promising_bound_apply(gate_case, replay):
    case = gate_case(decision="ask_human", replay=replay)
    payload = cli_harness.gate_check(case.claude_event, db=case.db)

    reason = _assert_candidate_hold(payload)
    assert payload["tier"] == "consulted_review_unavailable"
    assert payload["risk_summary"] == {"decision": "ask_human", **_SCORES}
    assert "reassess this candidate" in reason.lower()
    assert "pebra accept-risk --apply" not in reason


def test_one_byte_candidate_mismatch_never_reuses_stale_risk_math(gate_case):
    case = gate_case(decision="revise_safer")
    payload = cli_harness.gate_check(case.mismatch_event, db=case.db)

    assert payload["schema_version"] == 1
    assert payload["permission"] == "deny"
    assert payload["tier"] == "candidate_mismatch"
    assert payload["risk_summary"] is None
    assert payload["matched_assessment_id"] is None
    assert "does not match the exact candidate" in payload["reason"].lower()
    assert "expected loss" not in payload["reason"].lower()
    assert "benefit" not in payload["reason"].lower()
    assert "rau" not in payload["reason"].lower()
    assert all(str(value) not in payload["reason"] for value in _SCORES.values())


@pytest.mark.parametrize(
    "scores",
    (
        {"expected_loss": 0.75, "benefit": 0.2},
        {"expected_loss": float("inf"), "benefit": 0.2, "rau": -0.55},
        {"expected_loss": True, "benefit": 0.2, "rau": -0.55},
    ),
    ids=("partial", "nonfinite", "boolean"),
)
def test_malformed_scores_stay_blocking_without_numeric_fragments(gate_case, scores):
    case = gate_case(decision="revise_safer", scores=scores)
    payload = cli_harness.gate_check(case.claude_event, db=case.db)

    reason = _assert_candidate_hold(payload)
    assert payload["tier"] == "consulted_revise"
    assert payload["risk_summary"] is None
    assert payload["matched_assessment_id"] == "asm_1"
    assert "risk summary unavailable" in reason.lower()
    assert re.search(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?", reason) is None


@pytest.mark.parametrize("value", (10**1000, -(10**1000)), ids=("positive", "negative"))
def test_oversized_integer_scores_keep_cli_and_hook_clean_and_restrictive(gate_case, value):
    scores = {"expected_loss": value, "benefit": 0.2, "rau": -0.55}
    case = gate_case(decision="revise_safer", scores=scores)

    cli_payload = cli_harness.gate_check(case.claude_event, db=case.db)
    hook_payload = _gate_hook(case.claude_event, db=case.db)["hookSpecificOutput"]

    reason = _assert_candidate_hold(cli_payload)
    assert cli_payload["tier"] == "consulted_revise"
    assert cli_payload["risk_summary"] is None
    assert "risk summary unavailable" in reason.lower()
    assert hook_payload["permissionDecision"] == "deny"
    assert "risk summary unavailable" in hook_payload["permissionDecisionReason"].lower()


@pytest.mark.parametrize("persisted_decision", (None, "unknown_decision"))
def test_corrupt_persisted_decision_fails_open_with_visible_warning(
    gate_case, persisted_decision,
):
    case = gate_case(decision=persisted_decision)

    payload = cli_harness.gate_check(case.claude_event, db=case.db)
    hook_payload = _gate_hook(case.claude_event, db=case.db)

    assert payload["permission"] == "allow"
    assert payload["tier"] == "fail_open"
    assert payload["risk_summary"] is None
    assert payload["matched_assessment_id"] is None
    assert "persisted decision" in payload["warn"].lower()
    assert "integrity" in payload["warn"].lower()
    assert "hookSpecificOutput" not in hook_payload
    assert "persisted decision" in hook_payload["systemMessage"].lower()
