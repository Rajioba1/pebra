"""`pebra dashboard` (Phase 3b/5c-E) — launch the local Risk Observatory (read-only viewer).

Surface: resolves the repo + db, then LAZY-imports the FastAPI server inside run() so every other
command (and the dep-light golden CLI) stays importable without the web stack installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pebra.adapters.repository_registry import RepositoryRegistry


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "dashboard", help="Launch the local Risk Observatory (read-only viewer)."
    )
    p.add_argument("--repo-root", default=None)
    p.add_argument("--db", default=None)
    p.add_argument(
        "--repo-id", default=None,
        help="Override the resolved repo_id (for replaying a db copied from another path/machine).",
    )
    p.add_argument("--host", default="127.0.0.1")
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
    registry = RepositoryRegistry()
    repo = registry.resolve(args.repo_root or ".")
    db_path = args.db or str(Path(repo.repo_root) / ".pebra" / "pebra.db")
    # lazy: FastAPI/uvicorn are only needed to actually serve — never at CLI import time.
    from pebra.dashboard.server import resolve_dashboard_token, serve

    auth_mode = "token" if args.token else args.auth
    try:
        token = resolve_dashboard_token(args.host, auth_mode)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)  # fail loudly, before binding anything
        return 1

    serve(
        db_path,
        host=args.host,
        requested_port=args.port,
        instance=args.instance,
        token=token,
        repo_id=args.repo_id or repo.repo_id,
        repo_root=repo.repo_root,
        open_browser=args.open,
    )
    return 0
