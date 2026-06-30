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
    return "\n".join(lines) + "\n"


def write_report(results: list[FeatureResult], *, out_dir: Path, run_id: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run_id}.md"
    path.write_text(render_report(results, run_id=run_id), encoding="utf-8")
    return path
