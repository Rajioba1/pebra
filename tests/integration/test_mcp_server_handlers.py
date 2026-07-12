"""Phase 3c — MCP stdio server handlers.

The `mcp` SDK is lazy-imported only inside ``serve()``, so ``pebra.mcp_server.server`` imports (and
every handler) work in the dep-light nox env without the SDK. These tests call the handler functions
directly (no stdio) and exercise the real composition wiring against a temp repo, mirroring the CLI
surfaces — the parity guard for assess/compare/verify/accept-risk/record-outcome.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pebra.adapters.candidate_binding import binding_for_patch
from pebra.mcp_server import server

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "examples" / "login_patch.json"
REQUIRED_CHECK = "run targeted tests for the touched scope before commit"
_BOUND_PATCH = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
 def validate_login(u, p):
-    return True
+    return bool(u and p)
"""


def _git(cwd, *args) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def validate_login(u, p):\n    return True\n", encoding="utf-8"
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "src/auth.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _request() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _common(repo) -> dict:
    return {"repo_root": str(repo), "db": str(repo / "pebra.db")}


def _assess_args(repo) -> dict:
    req = _request()
    return {
        "task": req["task"],
        "action": req["candidate_actions"][0],
        "evidence": req["evidence"],
        "thresholds": req["thresholds"],
        **_common(repo),
    }


# --- module import is SDK-free ------------------------------------------------


def test_module_imports_without_mcp_sdk() -> None:
    assert callable(server.serve)
    assert set(server._HANDLERS) == {
        "pebra_assess", "pebra_compare", "pebra_verify",
        "pebra_accept_risk", "pebra_record_outcome",
    }


def test_tool_schemas_are_well_formed() -> None:
    assert set(server._TOOL_SCHEMAS) == set(server._HANDLERS)
    for spec in server._TOOL_SCHEMAS.values():
        assert spec["description"]
        assert spec["inputSchema"]["type"] == "object"


# --- assess (flat single-action short form) -----------------------------------


def test_assess_reproduces_worked_example(tmp_path) -> None:
    payload = server._handle_assess(_assess_args(tmp_path))
    assert payload["recommended_decision"] == "proceed"
    assert payload["requires_confirmation"] is True
    assert payload["risk_mode"] == "sensitive_context"
    assert payload["assessment_id"]
    s = payload["scores"]
    assert round(s["rau"], 2) == 0.31
    assert round(s["edit_confidence"], 2) == 0.83


# --- compare (full multi-action request) --------------------------------------


def test_compare_rejects_non_object_actions(tmp_path) -> None:
    # a bare string in candidate_actions must raise ValueError (caught by the dispatcher), not an
    # uncaught AttributeError that would break the stdio frame.
    with pytest.raises(ValueError):
        server._handle_compare(
            {"task": "t", "candidate_actions": ["bad"], **_common(tmp_path)}
        )


def test_assess_rejects_non_object_action(tmp_path) -> None:
    with pytest.raises(ValueError):
        server._handle_assess({"task": "t", "action": "bad", **_common(tmp_path)})


def test_compare_returns_all_scored_actions(tmp_path) -> None:
    req = _request()
    payload = server._handle_compare(
        {
            "task": req["task"],
            "candidate_actions": req["candidate_actions"],
            "evidence": req["evidence"],
            "thresholds": req["thresholds"],
            **_common(tmp_path),
        }
    )
    assert len(payload["scored_actions"]) == 1
    assert payload["scored_actions"][0]["action_id"] == "a1"
    assert payload["recommended"]["recommended_decision"] == "proceed"


# --- accept_risk --------------------------------------------------------------


def test_accept_risk_returns_sanction_id(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    action = _request()["candidate_actions"][0]
    binding = binding_for_patch(repo, _BOUND_PATCH)
    payload = server._handle_accept_risk(
        {
            "sanction_spec": {
                "assessment_id": "asm_1",
                "action_id": action["id"],
                "risk_profile": {
                    "assessment_id": "asm_1",
                    "action_id": action["id"],
                    "candidate_binding": binding,
                },
            },
            **_common(repo),
        }
    )
    assert payload["sanction_id"]
    assert payload["repo_id"]


def test_accept_risk_without_profile_raises(tmp_path) -> None:
    with pytest.raises(ValueError):
        server._handle_accept_risk({"sanction_spec": {}, **_common(tmp_path)})


# --- record_outcome -----------------------------------------------------------


def test_record_outcome_then_duplicate_raises(tmp_path) -> None:
    asm = server._handle_assess(_assess_args(tmp_path))["assessment_id"]
    common = _common(tmp_path)
    out = server._handle_record_outcome(
        {"assessment_id": asm, "status": "skipped", **common}
    )
    assert out["recorded"] is True
    with pytest.raises(ValueError):
        server._handle_record_outcome({"assessment_id": asm, "status": "rejected", **common})


def test_record_outcome_unknown_assessment_raises(tmp_path) -> None:
    with pytest.raises(KeyError):
        server._handle_record_outcome(
            {"assessment_id": "asm_999", "status": "skipped", **_common(tmp_path)}
        )


# --- verify (needs a real git repo) -------------------------------------------


def test_verify_in_envelope_proceeds(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    asm = server._handle_assess(_assess_args(repo))["assessment_id"]
    (repo / "src" / "auth.py").write_text(
        "def validate_login(u, p):\n    return bool(u and p)\n", encoding="utf-8"
    )
    _git(repo, "add", "src/auth.py")
    payload = server._handle_verify(
        {
            "assessment_id": asm,
            "scope": "staged",
            "completed_checks": {REQUIRED_CHECK: "passed"},
            **_common(repo),
        }
    )
    assert payload["pre_commit_decision"] == "proceed"
    assert payload["guardrails_id"]
