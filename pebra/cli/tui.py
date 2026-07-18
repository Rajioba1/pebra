"""`pebra tui` — launch the Observatory terminal dashboard (read-only viewer).

Surface: resolves the repo + db via the shared ObservatoryContext (same guarantees as `pebra dashboard`),
then LAZY-imports the Textual app inside run() so every other command (and ordinary CLI parsing/help) stays
importable without Textual on the import path. Read-only viewer only — no HTTP/serving flags.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from pebra.observatory_context import (
    ObservatoryContext,
    ObservatoryContextError,
    resolve_observatory_context,
)


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "tui", help="Launch the Observatory terminal dashboard (read-only viewer)."
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.add_argument(
        "--repo-id", default=None,
        help="Override the resolved repo_id (for replaying a db copied from another path/machine).",
    )
    p.add_argument(
        "--read-only", action="store_true",
        help="Open the db with SQLite mode=ro: no writes and NO .pebra/ init. Requires --db and --repo-id.",
    )
    p.set_defaults(func=run)


def _launch(context: ObservatoryContext) -> None:
    # lazy: Textual is only needed to actually run the TUI — never at CLI import/parse time.
    from pebra.tui.app import run_observatory

    run_observatory(context)


def run(args: Any, *, launch: Callable[[ObservatoryContext], None] = _launch) -> int:
    try:
        context = resolve_observatory_context(
            read_only=args.read_only, db=args.db, repo_id=args.repo_id, repo_root=args.repo_root
        )
    except ObservatoryContextError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    launch(context)
    return 0
