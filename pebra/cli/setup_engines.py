"""`pebra setup-engines` — unified CodeGraph + RCA readiness (explicit operator intent).

Never runs from assess/verify/gate/MCP/dashboard/TUI. Never executes Cargo or downloads RCA.
"""

from __future__ import annotations

import json
from typing import Any

from pebra.adapters.rca_adapter import probe_rca
from pebra.cli.setup_graph import ensure_graph_ready
from pebra.core.graph_version import in_accepted_range
from pebra.core.rca_engine_paths import build_rca_remediation


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "setup-engines",
        help="Prepare CodeGraph and report RCA readiness for measured benefit.",
    )
    p.add_argument("--repo-root", default=".", help="Repository path (defaults to current directory).")
    p.add_argument(
        "--fix",
        action="store_true",
        help="Authorize destructive CodeGraph repair (worktree mismatch / reindex).",
    )
    p.add_argument(
        "--via",
        choices=("auto", "standalone", "npm"),
        default="auto",
        help="CodeGraph install route (same as setup-graph).",
    )
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit one JSON document on stdout.")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    graph = ensure_graph_ready(
        args.repo_root,
        fix=bool(args.fix),
        via=args.via,
        version=None,
        allow_unsupported=False,
        as_json=False,
        explicit_version=False,
        silent=bool(args.as_json),  # one JSON document on stdout; install helpers stay quiet
    )
    rca = probe_rca()
    remediation = build_rca_remediation()
    in_range = bool(graph.version) and in_accepted_range(str(graph.version))
    graph_trusted = bool(graph.ok and graph.diagnosis.get("healthy") and in_range)
    ok = graph_trusted and rca.accepted
    # Honest degraded assess remains available even when engines are incomplete.
    assess_ready = True
    payload = {
        "command": "setup-engines",
        "ok": ok,
        "assess_ready": assess_ready,
        "graph": {
            "status": "available" if graph_trusted else "unavailable",
            "action": graph.action,
            "trusted": graph_trusted,
            "version": graph.version,
            "error": graph.error,
            "remediation": graph.remediation,
        },
        "rca": {
            "status": rca.status,
            "accepted": rca.accepted,
            "version": rca.version,
            "accepted_version": rca.accepted_version,
            "required_source_revision": rca.required_source_revision,
            "benefit_mode": rca.benefit_mode,
            "reason": rca.reason,
            "remediation": remediation,
        },
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        lines = [
            f"setup-engines — repo: {args.repo_root}",
            f"  CodeGraph: action={graph.action} trusted={graph_trusted} version={graph.version}",
        ]
        if graph.error:
            lines.append(f"    error: {graph.error}")
        if graph.remediation:
            lines.append(f"    remediation: {graph.remediation}")
        lines.append(
            f"  RCA: status={rca.status} accepted={rca.accepted} "
            f"benefit_mode={rca.benefit_mode}"
        )
        if not rca.accepted:
            lines.append(
                "    Risk still works; maintainability benefit is projected until RCA is accepted."
            )
            if not remediation["cargo_available"]:
                lines.append(f"    prerequisite: {remediation['prerequisite']}")
            lines.append(f"    install: {remediation['command']}")
            if remediation["cargo_available"]:
                lines.append("    Cargo is available on PATH to run the install command.")
            else:
                lines.append("    Cargo was not found on PATH; install rustup first, then re-run.")
        if assess_ready and not ok:
            lines.append(
                "  exit 1 means engines are incomplete, not that PEBRA is broken "
                "(assess_ready=true for degraded assessment)."
            )
        print("\n".join(lines))
    return 0 if ok else 1
