"""`pebra accept-risk` (Architecture AD-26) — surface to create a controlled-high-risk sanction."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pebra import composition
from pebra.adapters import git_adapter
from pebra.adapters.sanction_store import SanctionStore
from pebra.app import accept_risk_controller, human_approval_controller


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "accept-risk",
        help="Create a sanction from JSON or interactively approve and apply a pending candidate.",
    )
    p.add_argument("sanction_file", nargs="?", help="Path to the sanction spec JSON.")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Interactively approve, reassess, and apply one pending exact candidate.",
    )
    p.add_argument(
        "--assessment-id",
        default=None,
        help="Select a pending assessment when more than one requires approval.",
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    if getattr(args, "apply", False):
        if args.sanction_file:
            raise ValueError("--apply cannot be combined with a sanction file")
        return _run_apply(args)
    if not args.sanction_file:
        raise ValueError("a sanction file is required unless --apply is used")
    spec = json.loads(Path(args.sanction_file).read_text(encoding="utf-8"))
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        sid = accept_risk_controller.accept_risk(
            ctx.repo.repo_id, spec, sanction_port=SanctionStore(ctx.store)
        )
        print(json.dumps({"sanction_id": sid, "repo_id": ctx.repo.repo_id}, indent=2))
    finally:
        ctx.store.close()
    return 0


def _render_approval(summary: dict[str, Any]) -> str:
    values = summary["risk_benefit"]
    controls = ", ".join(summary["required_controls"]) or "none"
    files = ", ".join(summary["files"])
    return "\n".join([
        "PEBRA Human Approval",
        f"Assessment: {summary['assessment_id']}",
        f"Task: {summary['task']}",
        f"Files: {files}",
        f"Expected loss: {values['expected_loss']}",
        f"Benefit: {values['benefit']}",
        f"Expected utility: {values['expected_utility']}",
        f"RAU: {values['rau']}",
        f"Reason: {summary['reason'] or 'not recorded'}",
        f"Required controls: {controls}",
    ])


def _run_apply(args: Any) -> int:
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        head = git_adapter.head_commit(ctx.repo.repo_root)
        if not head:
            raise RuntimeError("the current Git HEAD could not be resolved")
        application_ports = composition.build_candidate_apply_ports(ctx)
        pending = human_approval_controller.select_pending_approval(
            repo_id=ctx.repo.repo_id,
            assessed_commit=head,
            assessment_id=args.assessment_id,
            store=ctx.store,
            replay_cache=application_ports["replay_cache"],
        )
        print(_render_approval(pending.summary))
        if not sys.stdin.isatty():
            raise RuntimeError("human approval requires an interactive terminal")
        if input(
            "Type APPROVE to confirm the listed controls and authorize this exact candidate: "
        ) != "APPROVE":
            print("Approval cancelled.")
            return 1
        outcome = human_approval_controller.approve_and_apply(
            pending,
            repo_id=ctx.repo.repo_id,
            repo_root=ctx.repo.repo_root,
            db_path=ctx.db_path,
            store=ctx.store,
            assess_ports=composition.build_assess_ports(pending.replay.request, ctx),
            application_ports=application_ports,
        )
        print(json.dumps({
            "sanction_id": outcome.sanction_id,
            "reassessment_id": outcome.reassessment_id,
            "status": "applied",
            "changed_files": list(outcome.changed_files),
        }, indent=2, sort_keys=True))
        return 0
    finally:
        ctx.store.close()
