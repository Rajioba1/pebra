"""`pebra dashboard` (Phase 3b/5c-E) — launch the local Risk Observatory (read-only viewer).

Surface: resolves the repo + db, then LAZY-imports the FastAPI server inside run() so every other
command (and the dep-light golden CLI) stays importable without the web stack installed.
"""

from __future__ import annotations

import sys
from typing import Any

from pebra import composition
from pebra.observatory_context import ObservatoryContextError, resolve_observatory_context


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "dashboard", help="Launch the local Risk Observatory (read-only viewer)."
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.add_argument(
        "--repo-id", default=None,
        help="Override the resolved repo_id (for replaying a db copied from another path/machine).",
    )
    p.add_argument(
        "--read-only", action="store_true",
        help="Serve the db with SQLite mode=ro: no schema/data writes and NO .pebra/ init. "
             "Requires --db and --repo-id (skips repo-root resolution). For strict filesystem isolation "
             "from a WAL db, serve a copied db instead of the live clone file.",
    )
    p.add_argument(
        "--host", default="127.0.0.1",
        help="Network interface to bind (default: 127.0.0.1).",
    )
    p.add_argument(
        "--port", type=int, default=None,
        help="Pin a port (0 = OS-assigned). Default: auto-increment from 9473.",
    )
    p.add_argument(
        "--instance", type=int, default=0, help="Parallel instance (port base 9473 + N*100)."
    )
    p.add_argument(
        "--auth", choices=["auto", "token", "none"], default="auto",
        help="auto: no token on loopback, token on a network bind. token: always require a bearer. "
             "none: no token (loopback only — refuses a network bind).",
    )
    p.add_argument(
        "--token", action="store_true", help="Force a bearer token even on loopback (alias for --auth token)."
    )
    p.add_argument("--open", action="store_true", help="Open the dashboard URL in a browser.")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    try:
        ctx = resolve_observatory_context(
            read_only=args.read_only, db=args.db, repo_id=args.repo_id, repo_root=args.repo_root
        )
    except ObservatoryContextError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # lazy: FastAPI/uvicorn are only needed to actually serve — never at CLI import time.
    from pebra.dashboard.server import resolve_dashboard_token, serve

    auth_mode = "token" if args.token else args.auth
    try:
        token = resolve_dashboard_token(args.host, auth_mode)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)  # fail loudly, before binding anything
        return 1

    graph_reader = composition.prepare_dashboard_graph_reader(
        ctx.repo_root, read_only=ctx.read_only
    )
    serve(
        ctx.db_path,
        host=args.host,
        requested_port=args.port,
        instance=args.instance,
        token=token,
        repo_id=ctx.repo_id,
        repo_root=ctx.repo_root,
        read_only=ctx.read_only,
        open_browser=args.open,
        graph_reader=graph_reader,
    )
    return 0
