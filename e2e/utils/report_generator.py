"""Per-run human-review report — the artifact a human reads after an e2e run.

Mirrors the Tauri convention: deterministic results are machine-asserted; visual results (dashboard
screenshots) are flagged NEEDS-HUMAN-REVIEW with a link the human eyeballs. ``render_report`` is a pure
string build (unit-tested); ``write_report`` does the IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_STATUSES = ("PASS", "NEEDS-HUMAN-REVIEW", "FAIL")


@dataclass
class FeatureResult:
    feature_name: str
    status: str  # one of _STATUSES
    lane: str
    transcript: Any | None = None
    screenshot_path: str | None = None
    notes: str = ""
    graph_evidence: dict[str, Any] | None = None
    learning_evidence: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"invalid feature status {self.status!r}; expected one of {_STATUSES}")


def _overall(results: list[FeatureResult]) -> str:
    statuses = {r.status for r in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "NEEDS-HUMAN-REVIEW" in statuses:
        return "NEEDS-HUMAN-REVIEW"
    return "PASS"


def _fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _evidence_lines(r: FeatureResult) -> list[str]:
    lines: list[str] = []
    graph = r.graph_evidence or {}
    if "engine" in graph or "operation" in graph or "file_fanin_percentile" in graph:
        lines.extend(
            [
                f"  - Graph engine: {graph.get('engine', 'unknown')}",
                f"  - Graph freshness: {graph.get('freshness', 'unknown')}",
                f"  - Changed operation: {graph.get('operation', 'unknown')}",
                "  - File fan-in rollup: "
                f"{_fmt_float(graph.get('file_fanin_percentile'))} percentile",
                f"  - Graph callers/references: {graph.get('caller_count', 'unknown')}",
                f"  - Risk event added: {graph.get('risk_event', 'none')}",
                f"  - Graph risk boost: +{_fmt_float(graph.get('risk_boost'))} p_event",
                "  - Final dependency-break probability: "
                f"{_fmt_float(graph.get('final_probability'))}",
            ]
        )
    attribution = graph.get("attribution") or {}
    if attribution:
        if attribution.get("implements_edge"):
            impl_line = (
                f"{attribution.get('broken_symbol', '?')} implements "
                f"{attribution.get('interface', '?')}"
            )
        else:
            impl_line = "not found in graph"
        lines.extend(
            [
                # callers (fan-in) and broken files are DISTINCT relationships — never a subset match:
                f"  - Attribution method: {attribution.get('attribution_method', 'unknown')}",
                f"  - Attribution confidence: {_fmt_float(attribution.get('attribution_confidence'))}",
                f"  - Implements edge: {impl_line}",
                f"  - Predicted callers (pre-edit fan-in): {attribution.get('predicted_callers', 'unknown')}",
                f"  - Materialized breakage: {attribution.get('actual_broken_files', 'unknown')} file(s)",
                "  - Method-level match (heuristic): "
                f"{'yes' if attribution.get('method_match') else 'no'}",
                f"  - Unresolved diagnostics: {attribution.get('unresolved_count', 'unknown')}",
                f"  - Graph freshness at attribution: {attribution.get('graph_freshness', 'unknown')}",
            ]
        )
    learning = r.learning_evidence or {}
    if learning:
        lines.extend(
            [
                f"  - Prior success estimate: {_fmt_float(learning.get('prior_success'))}",
                f"  - Learned success estimate: {_fmt_float(learning.get('learned_success'))}",
                f"  - Decision before learning: {learning.get('before_decision', 'unknown')}",
                f"  - Decision after learning: {learning.get('after_decision', 'unknown')}",
                "  - Promotion evidence: "
                f"n={learning.get('promotion_n', 'unknown')} completed outcomes",
                f"  - Real build outcomes: {learning.get('real_build_cycles', 'unknown')}",
                f"  - Seeded outcomes: {learning.get('seeded_cycles', 'unknown')}",
            ]
        )
    return lines


def render_report(results: list[FeatureResult], *, run_id: str) -> str:
    lines = [
        f"# PEBRA e2e run `{run_id}`",
        "",
        f"**OVERALL: {_overall(results)}**",
        "",
        "> Scope: agent-CLI seeded-learning + dashboard-visual. NOT full Tauri-level coverage — that",
        "> additionally requires the codegraph graph feature and the organic learning lane.",
        "",
        "| feature | lane | status | notes |",
        "|---|---|---|---|",
    ]
    for r in results:
        note = r.notes or ""
        if r.screenshot_path:
            note = f"{note} · screenshot: `{r.screenshot_path}`".strip(" ·")
        lines.append(f"| {r.feature_name} | {r.lane} | {r.status} | {note} |")
    evidence = [line for r in results for line in _evidence_lines(r)]
    if evidence:
        lines.extend(["", "## Human-readable evidence", ""])
        lines.extend(evidence)
    return "\n".join(lines) + "\n"


def write_report(results: list[FeatureResult], *, out_dir: Path, run_id: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run_id}.md"
    path.write_text(render_report(results, run_id=run_id), encoding="utf-8")
    return path
