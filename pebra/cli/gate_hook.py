"""`pebra gate-hook` — Claude Code PreToolUse enforcement shim over the universal gate decision.

Reads a PreToolUse event on stdin, asks ``gate_check_adapter.decide`` for a decision, and maps it to
Claude's PreToolUse output:
- ``deny`` / ``ask`` -> a ``hookSpecificOutput.permissionDecision`` JSON on stdout, so Claude blocks
  (or asks before) the edit, with the actionable reason.
- ``allow`` / ``pass`` / ``fail_open`` -> emit nothing (defer to Claude's normal permission flow).

It ALWAYS exits 0 and NEVER raises: a broken gate must never block a coding session (fail-open). The
decision logic lives entirely in the adapter; this module is only the host-specific output mapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pebra.adapters import gate_check_adapter as gca


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "gate-hook",
        help="Claude PreToolUse enforcement shim: deny/ask an unassessed high-impact edit via gate-check.",
    )
    p.add_argument("--db", default=None, help="Override the assessment store path.")
    p.add_argument(
        "--capabilities",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    p.set_defaults(func=run_gate_hook)


def run_gate_hook(args: Any) -> int:
    if getattr(args, "capabilities", False):
        print(json.dumps({
            "candidate_binding_protocol": "sha256-normalized-content-v1",
            "complete_candidate_event_required": True,
        }, sort_keys=True))
        return 0
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            return 0
        decision = gca.decide(event, db_path=getattr(args, "db", None))
    except Exception:  # noqa: BLE001 - the hook must never crash a host edit; any failure == silent allow
        return 0
    if decision.permission in ("deny", "ask"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.permission,
            "permissionDecisionReason": decision.reason or "PEBRA pre-edit gate",
        }}))
    elif decision.warn:
        # Claude supports a non-blocking systemMessage. Keep the existing fail-open policy, but do not
        # hide that enforcement degraded because graph/git/store evidence was unavailable.
        print(json.dumps({"systemMessage": decision.warn}))
    return 0
