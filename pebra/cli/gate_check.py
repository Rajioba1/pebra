"""`pebra gate-check` — the universal must-consult gate decision as a stdin/stdout query.

Reads a PreToolUse-style host event as JSON on stdin and prints the decision
(``{schema_version, permission, tier, reason, warn, risk_summary}``) as JSON. It is a pure QUERY: it
always exits 0 and never blocks by itself — the host-specific enforcement wrappers (Claude
``gate-hook``, Codex ``apply_patch`` hook,
pre-commit, the A/B write dispatch) turn a ``deny``/``ask`` into an actual block. Fail-open: unreadable
stdin -> allow. It imports no ``app`` or ``composition`` code; the decision lives in the adapter.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pebra.adapters import gate_check_adapter as gca
from pebra.core.gate_contract import GatePermission, GateTier


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "gate-check",
        help="Read a host edit event on stdin; print the pre-edit gate decision (allow/deny/ask) as JSON.",
    )
    p.add_argument("--db", default=None, help="Override the assessment store path (default: <repo>/.pebra/pebra.db).")
    p.add_argument("--consult-only", action="store_true",
                   help="Stop at must-consult; skip the ask verdict tier (for hosts with no human approver, e.g. the A/B runner).")
    p.add_argument(
        "--include-host-metadata", action="store_true",
        help="Include the exact matched assessment id for trusted host attribution.",
    )
    p.set_defaults(func=run_gate_check)


def run_gate_check(args: Any) -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps(gca.GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn="gate: unreadable event",
        ).as_dict()))
        return 0
    if not isinstance(event, dict):
        print(json.dumps(gca.GateDecision(
            GatePermission.CONTINUE,
            GateTier.FAIL_OPEN,
            warn="gate: event must be a JSON object",
        ).as_dict()))
        return 0
    decision = gca.decide(event, db_path=getattr(args, "db", None),
                          consult_only=getattr(args, "consult_only", False))
    print(json.dumps(decision.as_dict(
        include_host_metadata=getattr(args, "include_host_metadata", False)
    )))
    return 0
