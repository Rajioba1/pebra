"""`pebra record-outcome` (Phase 3a, AD-4) — record the terminal outcome of an assessed action.

Surface: wires the SQLite store as the OutcomePort and routes through record_outcome_controller. The
write is append-only; it never mutates the original (hash-chained) assessment row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pebra.adapters.repository_registry import RepositoryRegistry
from pebra.adapters.store.db import SqliteStore
from pebra.app import record_outcome_controller


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "record-outcome", help="Record the terminal outcome of an assessed action."
    )
    p.add_argument("--assessment-id", required=True, help="The stored assessment id (e.g. asm_1).")
    p.add_argument(
        "--status", required=True, choices=["completed", "skipped", "rejected"],
        help="Terminal action status to record.",
    )
    p.add_argument(
        "--detail", default=None,
        help="Optional JSON describing the actual result. Recognized learning labels (Milestone 4b): "
        'actual_success (bool), event_outcomes ({event: bool}), benefit_realized (bool), '
        "actual_review_cost (number), actual_rework_cost (number). Absent labels stay censored.",
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    registry = RepositoryRegistry()
    repo = registry.resolve(args.repo_root or ".")
    db_path = args.db or str(Path(repo.repo_root) / ".pebra" / "pebra.db")
    try:
        detail = json.loads(args.detail) if args.detail else None
    except json.JSONDecodeError as exc:
        print(f"error: --detail is not valid JSON: {exc}", file=sys.stderr)
        return 2
    store = SqliteStore(db_path)
    try:
        record_outcome_controller.record_outcome(
            args.assessment_id,
            args.status,
            outcome_port=store,
            detail=detail,
            label_source="host",
        )
    except (KeyError, ValueError) as exc:
        # unknown assessment, or an outcome already recorded -> clean exit, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    print(f"Recorded outcome '{args.status}' for {args.assessment_id}.")
    return 0
