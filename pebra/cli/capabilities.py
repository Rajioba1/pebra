"""`pebra capabilities` — report measured language and enforcement posture for a repo (read-only).

Honest DECLARED ∩ MEASURED reporting: for each language actually present in the CodeGraph index, show
the support tier PEBRA can back with graph facts (full / partial / risk_only) and the coverage that
earned it. A language PEBRA is *built* to index but that isn't in this repo's graph simply won't
appear — the command never claims support it can't measure. Empty output = graph absent/uninitialized.
"""

from __future__ import annotations

import json
from typing import Any

from pebra import composition
from pebra.adapters import enforcement_capability
from pebra.core.agent_hosts import AGENT_HOSTS
from pebra.core.language_capability import DECLARED_LANGUAGES


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "capabilities",
        help="Report measured language support and observed host-enforcement posture for a repo.",
    )
    p.add_argument("--repo-root", default=".", help="Repository path (defaults to current directory).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    p.set_defaults(func=run_capabilities)


def run_capabilities(args: Any) -> int:
    rows = composition.probe_language_capabilities(args.repo_root)
    enforcement = enforcement_capability.probe(
        args.repo_root,
        graph_available=bool(rows),
    )
    if args.as_json:
        print(json.dumps(
            {"command": "capabilities", "repo_root": args.repo_root,
             "declared_languages": list(DECLARED_LANGUAGES), "measured": rows,
             "enforcement": enforcement},
            indent=2, sort_keys=True,
        ))
        return 0
    if not rows:
        print(f"capabilities - repo: {args.repo_root}\n  (no CodeGraph index / no indexed languages)")
    else:
        print(f"capabilities - repo: {args.repo_root}")
        print(f"  {'language':<14}{'tier':<11}{'nodes':>7}  sig%  vis%")
        for r in rows:
            print(
                f"  {r['language']:<14}{r['tier']:<11}{r['node_count']:>7}"
                f"  {r['signature_coverage_ratio']:>4.0%} {r['visibility_coverage_ratio']:>4.0%}"
            )
    print("  enforcement:")
    for host in (*AGENT_HOSTS, "mcp"):
        state = enforcement[host]
        reasons = f" ({', '.join(state['reasons'])})" if state["reasons"] else ""
        print(f"    {host:<7} {state['mode']}{reasons}")
    return 0
