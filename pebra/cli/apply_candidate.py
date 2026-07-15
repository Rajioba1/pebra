"""`pebra apply-candidate` — apply one exact, authorized assessed candidate."""

from __future__ import annotations

import json
from typing import Any

from pebra import composition
from pebra.app import candidate_apply_controller


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "apply-candidate",
        help="Apply the exact candidate cached for an authorized assessment.",
    )
    parser.add_argument(
        "--assessment-id", required=True,
        help="Authorized assessment whose exact cached candidate should be applied.",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="Repository path (defaults to current directory).",
    )
    parser.add_argument(
        "--db", default=None,
        help="SQLite store path (defaults to <repo>/.pebra/pebra.db).",
    )
    parser.set_defaults(func=run)


def run(args: Any) -> int:
    start_path = args.repo_root or "."
    ctx = composition.resolve_repo_and_db(start_path, args.db)
    try:
        outcome = candidate_apply_controller.apply_candidate(
            assessment_id=args.assessment_id,
            repo_id=ctx.repo.repo_id,
            repo_root=ctx.repo.repo_root,
            db_path=ctx.db_path,
            store=ctx.store,
            **composition.build_candidate_apply_ports(ctx),
        )
        print(json.dumps({
            "assessment_id": outcome.assessment_id,
            "status": "applied",
            "changed_files": list(outcome.changed_files),
        }, indent=2, sort_keys=True))
    finally:
        ctx.store.close()
    return 0
