"""`pebra gate-hook` — Claude Code PreToolUse enforcement shim over `gate-check`.

The shim maps a universal GateDecision to Claude's PreToolUse output: deny/ask -> a
``hookSpecificOutput.permissionDecision`` JSON (Claude blocks/asks); allow/pass/fail_open -> nothing
(defer to the normal permission flow). It must ALWAYS exit 0 and NEVER raise — a broken gate must not
block a session. These tests monkeypatch the decision so they exercise the shim's mapping, not the
(separately tested) gate logic.
"""

from __future__ import annotations

import io
import json

from pebra.adapters import gate_check_adapter as gca
from pebra.cli import gate_hook as gh_cmd
from pebra.cli.main import build_parser


def _run(stdin_text: str, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    rc = gh_cmd.run_gate_hook(build_parser().parse_args(["gate-hook"]))
    return rc, capsys.readouterr().out


def test_gate_hook_is_registered():
    args = build_parser().parse_args(["gate-hook"])
    assert args.func is gh_cmd.run_gate_hook


def test_gate_hook_capability_handshake_reports_candidate_binding_contract(capsys):
    args = build_parser().parse_args(["gate-hook", "--capabilities"])

    assert gh_cmd.run_gate_hook(args) == 0

    assert json.loads(capsys.readouterr().out) == {
        "candidate_binding_protocol": "sha256-normalized-content-v1",
        "complete_candidate_event_required": True,
    }


def test_deny_emits_permission_decision(monkeypatch, capsys):
    monkeypatch.setattr(gca, "decide",
                        lambda event, db_path=None: gca.GateDecision("deny", "must_consult", reason="run assess first"))
    rc, out = _run(json.dumps({"tool_name": "Edit"}), monkeypatch, capsys)
    payload = json.loads(out)["hookSpecificOutput"]
    assert rc == 0
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"
    assert payload["permissionDecisionReason"] == "run assess first"


def test_ask_emits_permission_decision(monkeypatch, capsys):
    monkeypatch.setattr(gca, "decide",
                        lambda event, db_path=None: gca.GateDecision("ask", "ask", reason="needs approval"))
    rc, out = _run(json.dumps({"tool_name": "Edit"}), monkeypatch, capsys)
    assert rc == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_allow_emits_nothing(monkeypatch, capsys):
    monkeypatch.setattr(gca, "decide", lambda event, db_path=None: gca.GateDecision("allow", "consulted"))
    rc, out = _run(json.dumps({"tool_name": "Edit"}), monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""  # defer to normal permission flow


def test_fail_open_surfaces_warning_without_blocking(monkeypatch, capsys):
    monkeypatch.setattr(
        gca,
        "decide",
        lambda event, db_path=None: gca.GateDecision(
            "allow", "fail_open", warn="graph evidence unavailable; enforcement degraded"
        ),
    )
    rc, out = _run(json.dumps({"tool_name": "Edit"}), monkeypatch, capsys)
    payload = json.loads(out)
    assert rc == 0
    assert payload == {"systemMessage": "graph evidence unavailable; enforcement degraded"}


def test_malformed_stdin_is_silent_allow(monkeypatch, capsys):
    rc, out = _run("not json", monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""


def test_non_dict_event_is_silent_allow(monkeypatch, capsys):
    rc, out = _run("[1,2,3]", monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""


def test_decision_error_is_silent_allow(monkeypatch, capsys):
    def _boom(event, db_path=None):
        raise RuntimeError("gate blew up")
    monkeypatch.setattr(gca, "decide", _boom)
    rc, out = _run(json.dumps({"tool_name": "Edit"}), monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""  # a crashing gate must never block the edit
