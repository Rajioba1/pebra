"""`pebra verify` (Architecture §9, plan §5) — post-edit autonomy-envelope enforcement.

Loads a stored assessment, compares the actual diff against its binding guidance via the guardrails,
persists a guardrails row, and renders the verify card (or canonical JSON).
"""

from __future__ import annotations

import json
from typing import Any

from pebra import composition
from pebra.app import verify_controller
from pebra.app.verify_controller import VerifyOutcome
from pebra.core.constants import Decision

_DECISION_TITLE = {
    Decision.PROCEED: "Proceed",
    Decision.INSPECT_FIRST: "Inspect First",
    Decision.TEST_FIRST: "Test First",
    Decision.REVISE_SAFER: "Revise Safer",
    Decision.ASK_HUMAN: "Ask Human",
    Decision.REJECT: "Reject",
}


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "verify", help="Check the actual diff against a stored assessment's approved envelope."
    )
    p.add_argument("--assessment-id", required=True, help="The stored assessment id (e.g. asm_1).")
    p.add_argument(
        "--scope", default="staged", choices=["staged", "all", "branch"],
        help="Diff scope: staged (index vs HEAD), all (working tree + untracked). "
        "NOTE: 'branch' currently behaves like working-tree-vs-HEAD; true branch-base "
        "comparison is refined in a later phase.",
    )
    p.add_argument(
        "--completed-check", action="append", default=[], metavar="CHECK=STATUS",
        help="Mark a required check as completed, e.g. --completed-check 'pytest -q'=passed",
    )
    p.add_argument(
        "--dry-run-preview", action="store_true",
        help="Assert an impact preview was produced (satisfies the dry-run-required check).",
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    p.set_defaults(func=run)


def _parse_completed(items: list[str]) -> dict[str, str]:
    completed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--completed-check must be CHECK=STATUS, got {item!r}")
        key, status = item.rsplit("=", 1)
        completed[key] = status
    return completed


def run(args: Any) -> int:
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        outcome = verify_controller.verify(
            args.assessment_id,
            scope=args.scope,
            completed_checks=_parse_completed(args.completed_check),
            dry_run_preview_present=args.dry_run_preview,
            repo_root=ctx.repo.repo_root,
            store=ctx.store,
            **composition.build_verify_ports(),
        )
        if args.as_json:
            print(json.dumps(composition.verify_payload(outcome), indent=2, sort_keys=True))
        else:
            print(render_verify_card(outcome))
        # non-zero exit when the envelope is violated, so CI / agents can gate on it
        return 0 if outcome.result.pre_commit_decision is Decision.PROCEED else 2
    finally:
        ctx.store.close()  # close even if the controller raises (e.g. unknown assessment id)


def render_verify_card(outcome: VerifyOutcome) -> str:
    r = outcome.result
    title = _DECISION_TITLE[r.pre_commit_decision]
    lines = [
        f"PEBRA Verify: {title}",
        "",
        f"Evidence:        {r.evidence_freshness}",
        f"Safe Scope:      {r.safe_scope_status}",
        f"Scope Drift:     {'yes' if r.scope_drift_detected else 'no'}",
        f"Symbol Mismatch: {'yes' if r.symbol_change_mismatch else 'no'}",
    ]
    if r.unexpected_files:
        lines.append(f"Unexpected:      {', '.join(r.unexpected_files)}")
    if outcome.invalidated_sanctions:
        lines.append(f"Sanctions:       invalidated {', '.join(outcome.invalidated_sanctions)}")
    if r.reasons:
        lines.append("")
        lines.append("Why:")
        lines.extend(f"  - {reason}" for reason in r.reasons)
    return "\n".join(lines)
