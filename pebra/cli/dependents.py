"""`pebra dependents` — list the files that depend on a target file (file-level blast radius, read-only).

Surfaces the codegraph reverse-edge query as concrete FILE PATHS (not just fan-in counts) so a caller —
e.g. a "blast-radius" safe-edit advisory — can tell an agent WHICH files reference the code it is about
to change. cli -> composition -> codegraph adapter; core stays pure. Empty list when the graph is absent.
"""

from __future__ import annotations

import json
from typing import Any

from pebra import composition


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "dependents",
        help="List files that depend on a target file (file-level blast radius; read-only).",
    )
    p.add_argument("--target", required=True, help="Repo-relative (or absolute) path of the file being changed.")
    p.add_argument("--repo-root", default=".", help="Repository path (defaults to current directory).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    p.set_defaults(func=run_dependents)


def run_dependents(args: Any) -> int:
    result = composition.dependent_files_result(args.repo_root, args.target)
    files = result.get("dependent_files", [])
    payload = {
        "command": "dependents",
        "repo_root": args.repo_root,
        "target": args.target,
        "available": bool(result.get("available")),
        "graph_freshness": result.get("graph_freshness"),
        "dependent_files": files,
        "count": int(result.get("count") or 0),
        "fallback_reason": result.get("fallback_reason"),
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"dependents - target: {args.target} ({len(files)} dependent file(s))")
        for f in files:
            print(f"  {f}")
    return 0
