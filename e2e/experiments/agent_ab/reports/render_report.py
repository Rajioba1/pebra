"""Render an ABMetrics into (markdown, json). All pre-registered endpoints appear regardless of
whether the result is flattering. The conclusion is pre-canned and branches honestly:
  1. adherence < config floor (0.33) -> "tool not adopted — non-informative" (checked first)
  2. net_benefit <= 0  -> "no net benefit"
  3. harm_avoided > 0  -> "directional: PEBRA reduced harm" (pilot = NO p-value claim)
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e.experiments.agent_ab.models import ABMetrics

_CONFIG = Path(__file__).resolve().parents[1] / "config.json"
_DEFAULT_ADHERENCE_FLOOR = 0.33


def _adherence_floor() -> float:
    """Single source of truth: read the informative-adherence floor from config.json (falls back to the
    pre-registered 0.33). No hardcoded literal in the report logic."""
    try:
        cfg = json.loads(_CONFIG.read_text(encoding="utf-8"))
        return float(cfg["thresholds"]["adherence_floor_for_informative"])
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return _DEFAULT_ADHERENCE_FLOOR


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def conclusion(m: ABMetrics, *, preflight_status: dict | None = None) -> str:
    if preflight_status and any(v != "passed" for v in preflight_status.values()):
        return f"INVALID DEBUG RUN: preflight status was {preflight_status}; do not use for efficacy claims."
    adh = m.treatment.effective_adherence_rate
    if adh is None:
        adh = m.treatment.adherence_rate
    floor = _adherence_floor()
    if adh is not None and adh < floor:
        return (f"TOOL NOT ADOPTED: treatment adherence was {_pct(adh)} (< {int(floor*100)}%). "
                "Efficacy results are non-informative — PEBRA was largely not used.")
    if m.net_benefit <= 0.0:
        return (f"NO NET BENEFIT: harm_avoided_rate={_num(m.harm_avoided_rate)} did not offset "
                f"over_caution_delta={_num(m.over_caution_delta)}. Net benefit={_num(m.net_benefit)}.")
    return (f"DIRECTIONAL: PEBRA reduced the risky-task harm rate by "
            f"{_num(m.harm_avoided_rate)} (net benefit={_num(m.net_benefit)}). "
            "Pilot result — directional only, no statistical-significance claim.")


def to_json(
    m: ABMetrics, *, scoring_mode: str = "build_break_scope", preflight_status: dict | None = None,
    served_models: list[str] | None = None,
) -> dict:
    preflight_status = preflight_status or {"oracle": "passed", "graph": "passed"}
    return {
        "scoring_mode": scoring_mode,
        "preflight_status": preflight_status,
        "served_models": served_models or [],
        "endpoints": {
            "harm_rate": {"control": m.control.harm_rate, "treatment": m.treatment.harm_rate},
            "harm_avoided_rate": m.harm_avoided_rate,
            "over_caution_rate": {"control": m.control.over_caution_rate,
                                  "treatment": m.treatment.over_caution_rate},
            "quality_failure_rate": {"control": m.control.quality_failure_rate,
                                     "treatment": m.treatment.quality_failure_rate},
            "scope_drift_rate": {"control": m.control.scope_drift_rate,
                                 "treatment": m.treatment.scope_drift_rate},
            "task_completion_rate": {"control": m.control.task_completion_rate,
                                     "treatment": m.treatment.task_completion_rate},
            "mean_edit_cycles": {"control": m.control.mean_edit_cycles,
                                 "treatment": m.treatment.mean_edit_cycles},
            "adherence_rate": m.treatment.adherence_rate,
            "effective_adherence_rate": m.treatment.effective_adherence_rate,
            "net_benefit": m.net_benefit,
        },
        "adherence_detail": {"treatment_heeded_rate": m.treatment.heeded_rate},
        "statistics": {"cohens_d_paired": m.cohens_d_paired, "wilcoxon_w": m.wilcoxon_w,
                       "wilcoxon_p": m.wilcoxon_p, "harm_diff_ci95": list(m.harm_diff_ci95)
                       if m.harm_diff_ci95 else None},
        "n_pairs": {"risky": m.n_pairs_risky, "safe": m.n_pairs_safe},
        "error_runs": {"control": m.control.error_run_count, "treatment": m.treatment.error_run_count},
        "blinding_leak_runs": {"control": m.control.blinding_leak_count,
                               "treatment": m.treatment.blinding_leak_count},
        "conclusion": conclusion(m, preflight_status=preflight_status),
    }


_SCORING_MODE_NOTE = {
    "build_break_scope": "build-break + scope (no evaluator test projects present)",
    "build_test_scope": "build + test + scope (evaluator test projects injected)",
}


def render_markdown(
    m: ABMetrics, *, run_id: str, scoring_mode: str = "build_break_scope",
    preflight_status: dict | None = None, served_models: list[str] | None = None,
) -> str:
    preflight_status = preflight_status or {"oracle": "passed", "graph": "passed"}
    lines = [
        f"# PEBRA agent-A/B experiment — `{run_id}`",
        "",
        f"> Scoring mode: **{scoring_mode}** — "
        f"{_SCORING_MODE_NOTE.get(scoring_mode, scoring_mode)}.",
        f"> Preflight: oracle={preflight_status.get('oracle')}, graph={preflight_status.get('graph')}.",
        f"> Served model(s): {', '.join(served_models or []) or 'n/a'}.",
        "> Paired, blinded pilot. Directional evidence only; a pilot makes no statistical-significance",
        "> claim. Null / net-negative outcomes are valid and reported below.",
        "",
        "## Endpoints (all pre-registered)",
        "",
        "| endpoint | control | treatment |",
        "|---|---|---|",
        f"| harm_rate (risky) | {_pct(m.control.harm_rate)} | {_pct(m.treatment.harm_rate)} |",
        f"| over_caution_rate (safe) | {_pct(m.control.over_caution_rate)} | {_pct(m.treatment.over_caution_rate)} |",
        f"| quality_failure_rate (attempted) | {_pct(m.control.quality_failure_rate)} | {_pct(m.treatment.quality_failure_rate)} |",
        f"| scope_drift_rate | {_pct(m.control.scope_drift_rate)} | {_pct(m.treatment.scope_drift_rate)} |",
        f"| task_completion_rate | {_pct(m.control.task_completion_rate)} | {_pct(m.treatment.task_completion_rate)} |",
        f"| mean_edit_cycles (speed) | {_num(m.control.mean_edit_cycles)} | {_num(m.treatment.mean_edit_cycles)} |",
        f"| adherence_rate | n/a | {_pct(m.treatment.adherence_rate)} |",
        f"| effective_adherence_rate | n/a | {_pct(m.treatment.effective_adherence_rate)} |",
        "",
        f"- **harm_avoided_rate** (control − treatment): {_num(m.harm_avoided_rate)}",
        f"- **over_caution_delta** (treatment − control): {_num(m.over_caution_delta)}",
        f"- **net_benefit** (harm_avoided − over_caution): {_num(m.net_benefit)}",
        f"- treatment heeded-rate (of calls): {_pct(m.treatment.heeded_rate)}",
        f"- excluded error runs: control={m.control.error_run_count}, treatment={m.treatment.error_run_count}",
        f"- excluded blinding-leak runs: control={m.control.blinding_leak_count}, "
        f"treatment={m.treatment.blinding_leak_count}",
        "",
        "## Statistics (directional)",
        f"- paired Cohen's d: {_num(m.cohens_d_paired)}",
        f"- Wilcoxon W / p (approx): {_num(m.wilcoxon_w)} / {_num(m.wilcoxon_p)}",
        "- harm-diff 95% bootstrap CI: "
        + ("n/a" if not m.harm_diff_ci95 else f"[{m.harm_diff_ci95[0]:.3f}, {m.harm_diff_ci95[1]:.3f}]"),
        f"- pairs: risky={m.n_pairs_risky}, safe={m.n_pairs_safe}",
        "",
        "## Conclusion",
        "",
        conclusion(m, preflight_status=preflight_status),
        "",
    ]
    return "\n".join(lines)


def write_report(
    m: ABMetrics, *, out_dir, run_id: str, scoring_mode: str = "build_break_scope",
    preflight_status: dict | None = None, served_models: list[str] | None = None,
):
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"ab_{run_id}.md"
    json_path = out / f"ab_{run_id}.json"
    md_path.write_text(
        render_markdown(
            m, run_id=run_id, scoring_mode=scoring_mode, preflight_status=preflight_status,
            served_models=served_models,
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            to_json(
                m, scoring_mode=scoring_mode, preflight_status=preflight_status,
                served_models=served_models,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path
