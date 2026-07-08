"""`pebra dashboard` (Phase 3b/5c-E) — launch the local Risk Observatory (read-only viewer).

Surface: resolves the repo + db, then LAZY-imports the FastAPI server inside run() so every other
command (and the dep-light golden CLI) stays importable without the web stack installed.
"""

from __future__ import annotations

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
    p.set_defaults(func=run)


def run(args: Any) -> int:
    registry = RepositoryRegistry()
    repo = registry.resolve(args.repo_root or ".")
    db_path = args.db or str(Path(repo.repo_root) / ".pebra" / "pebra.db")
    # lazy: FastAPI/uvicorn are only needed to actually serve — never at CLI import time.
    from pebra.dashboard.server import serve

    serve(
        db_path,
        host=args.host,
        requested_port=args.port,
        instance=args.instance,
        repo_id=args.repo_id or repo.repo_id,
        repo_root=repo.repo_root,
    )
    return 0
