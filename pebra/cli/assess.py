"""`pebra assess` (Architecture §3, plan §5) — surface: parse input, wire adapters, render output.

Composes the concrete Phase-0 adapters, runs ``assess_controller``, and renders either the human card
or canonical JSON. Surfaces never call ``core/`` directly — they go through the controller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pebra import composition
from pebra.app import assess_controller
from pebra.core import candidate_parser
from pebra.core.constants import Decision
from pebra.core.explanation_generator import Explanation
from pebra.core.models import AssessmentResult

_DECISION_TITLE = {
    Decision.PROCEED: "Proceed",
    Decision.INSPECT_FIRST: "Inspect First",
    Decision.TEST_FIRST: "Test First",
    Decision.REVISE_SAFER: "Revise Safer",
    Decision.ASK_HUMAN: "Ask Human",
    Decision.REJECT: "Reject",
}


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("assess", help="Assess a candidate edit from a JSON request file.")
    p.add_argument("request_file", help="Path to the assessment request JSON.")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit canonical JSON.")
    p.add_argument("--repo-root", default=None, help="Repo root (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    raw = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    request = candidate_parser.parse(raw)

    start_path = args.repo_root or "."
    ctx = composition.resolve_repo_and_db(start_path, args.db)
    try:
        outcome = assess_controller.assess(
            request,
            thresholds=request.thresholds,
            start_path=start_path,
            **composition.build_assess_ports(request, ctx),
        )
        if args.as_json:
            print(json.dumps(composition.assess_payload(outcome), indent=2, sort_keys=True))
        else:
            print(render_card(outcome.recommended_result, outcome.recommended_explanation))
    finally:
        ctx.store.close()  # close even if the controller raises (no leaked SQLite connection)
    return 0


def _fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _graph_evidence_lines(result: AssessmentResult) -> list[str]:
    sse = result.symbol_scope_evidence or {}
    rollup = sse.get("file_fanin_rollup")
    # Only claim graph evidence when CodeGraph actually answered. A DELETE with no engine still carries
    # a (non-empty) rollup dict with resolution_method='unresolved' — rendering "Graph engine: CodeGraph"
    # for it would falsely imply the graph was consulted (breaks the graph-vs-no-graph honesty contract).
    if not rollup or rollup.get("resolution_method") == "unresolved":
        return []
    op = str(sse.get("file_operation_kind", "unknown")).lower().replace("_", " ")
    if op == "delete":
        op = "delete file"
    return [
        "",
        "Graph Evidence:",
        "  - Graph engine: CodeGraph",
        f"  - Graph freshness: {rollup.get('graph_freshness', 'unknown')}",
        f"  - Changed operation: {op}",
        f"  - File fan-in rollup: {_fmt_float(rollup.get('percentile'))} percentile",
        f"  - Graph callers/references: {rollup.get('distinct_caller_count', 'unknown')}",
    ]


def render_card(result: AssessmentResult, ex: Explanation) -> str:
    title = _DECISION_TITLE[result.recommended_decision]
    if result.recommended_decision is Decision.PROCEED and result.requires_confirmation:
        title += " (confirmation required)"

    lines = [
        f"PEBRA Decision: {title}",
        "",
        f"Risk Level:        {ex.risk_level_band:<16}(used {ex.risk_budget_percent}% of the safe limit)",
        f"Confidence:        {ex.confidence_band.capitalize()} ({ex.confidence_percent}%)",
        f"Value After Risk:  {ex.value_after_risk_band}",
        f"Code Sensitivity:  {ex.code_sensitivity_label} - {ex.code_sensitivity_descriptor}",
        f"Expected Damage:   {ex.expected_damage:.2f}",
        "",
        "Why:",
    ]
    lines.extend(f"  - {line}" for line in ex.why)
    lines.extend(_graph_evidence_lines(result))
    return "\n".join(lines)
