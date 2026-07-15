"""`pebra graph-stats` - report CodeGraph node counts for a repo (read-only).

Exists so an operator - and the A/B experiment's graph preflight - can INDEPENDENTLY verify that a
"fresh" index actually picked up nodes. A freshly-built-but-empty graph (e.g. an index that parsed no
source) is a real failure mode that ``graph_freshness == "fresh"`` alone does NOT catch; asserting a
non-zero C# callable node count closes that gap. Counts are read via the codegraph adapter
(cli -> composition -> adapter; core stays pure). Zeros when the graph is absent/uninitialized.
"""

from __future__ import annotations

import json
from typing import Any

from pebra import composition


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "graph-stats",
        help="Report CodeGraph node counts (total / callable / C# callable) for a repo.",
    )
    p.add_argument("--repo-root", default=".", help="Repository path (defaults to current directory).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    p.set_defaults(func=run_graph_stats)


def run_graph_stats(args: Any) -> int:
    counts = composition.graph_node_counts(args.repo_root)
    payload = {"command": "graph-stats", "repo_root": args.repo_root, **counts}
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"graph-stats - repo: {args.repo_root}\n"
            f"  total nodes:    {counts['total']}\n"
            f"  callable nodes: {counts['callable']}\n"
            f"  C# callable:    {counts['csharp_callable']}"
        )
    return 0
