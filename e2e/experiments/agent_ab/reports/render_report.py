"""Render an ABMetrics into (markdown, json). All pre-registered endpoints appear regardless of
whether the result is flattering. The conclusion is pre-canned and branches honestly:
  1. adherence < config floor (0.33) -> "tool not adopted — non-informative" (checked first)
  2. net_benefit <= 0  -> "no net benefit"
  3. harm_avoided > 0  -> "directional: PEBRA reduced harm" (pilot = NO p-value claim)
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import ABMetrics, AssayMetrics

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


def _preflight_passed(preflight_status: dict) -> bool:
    return all(v == "passed" for v in preflight_status.values())


def _find_pair(m: AssayMetrics, intervention: str, baseline: str):
    for p in m.pairwise:
        if p.intervention_arm == intervention and p.baseline_arm == baseline:
            return p
    return None


def _assay_claim_context(
    m: AssayMetrics, run_metadata: dict | None
) -> dict:
    pair = _find_pair(m, models.ARM_PEBRA, models.ARM_SHAM)
    actual_pairs = pair.n_pairs_risky if pair is not None else 0
    minimum = models.MIN_PAIRS_FOR_EFFICACY
    seeds_per_arm = None
    run_intent = None
    if run_metadata is not None:
        try:
            seeds_per_arm = int(run_metadata["seeds_per_arm"])
        except (KeyError, TypeError, ValueError):
            seeds_per_arm = None
        run_intent = str(run_metadata.get("run_intent") or "") or None
    reasons: list[str] = []
    if run_metadata is None:
        reasons.append("run metadata unavailable")
    else:
        configured_minimum = run_metadata.get("minimum_pairs_for_efficacy")
        if configured_minimum is not None:
            try:
                configured_minimum = int(configured_minimum)
            except (TypeError, ValueError):
                configured_minimum = None
            if configured_minimum != minimum:
                reasons.append(
                    f"run metadata minimum {configured_minimum!r} differs from policy {minimum}"
                )
        if run_intent == "diagnostic":
            reasons.append("explicit diagnostic run intent")
        if seeds_per_arm is None:
            reasons.append("configured seed count unavailable")
        elif seeds_per_arm < minimum:
            reasons.append(f"configured seeds {seeds_per_arm} below minimum {minimum}")
    if actual_pairs < minimum:
        reasons.append(f"observed risky pairs {actual_pairs} below minimum {minimum}")
    diagnostic_only = bool(reasons)
    return {
        "seeds_per_arm": seeds_per_arm,
        "minimum_pairs_for_efficacy": minimum,
        "actual_pairs_for_efficacy": actual_pairs,
        "run_intent": run_intent,
        "diagnostic_only": diagnostic_only,
        "diagnostic_reasons": reasons,
    }


def _assay_report_state(
    m: AssayMetrics, preflight_status: dict, run_metadata: dict | None
) -> dict:
    raw_verdict = m.interpretation.verdict
    preflight_valid = _preflight_passed(preflight_status)
    structural_verdict = raw_verdict if preflight_valid else "INVALID_DEBUG_RUN"
    assay_valid = preflight_valid and not structural_verdict.startswith("INVALID_")
    claim_context = _assay_claim_context(m, run_metadata)
    diagnostic_only = bool(claim_context["diagnostic_only"])
    verdict = (
        models.VERDICT_DIAGNOSTIC_ONLY
        if assay_valid and diagnostic_only
        else structural_verdict
    )
    if verdict == models.VERDICT_DIAGNOSTIC_ONLY:
        reasons = "; ".join(claim_context["diagnostic_reasons"])
        conclusion = (
            f"DIAGNOSTIC ONLY ({reasons}): raw structural verdict {raw_verdict} was produced from "
            f"{claim_context['actual_pairs_for_efficacy']} risky PEBRA-vs-sham pair(s). "
            "Inspect arm behavior and traces; do not claim efficacy."
        )
    else:
        conclusion = _VERDICT_NOTE.get(verdict, "")
    claim_valid = assay_valid and not diagnostic_only
    return {
        **claim_context,
        "raw_verdict": raw_verdict,
        "structural_verdict": structural_verdict,
        "verdict": verdict,
        "preflight_valid": preflight_valid,
        "assay_valid": assay_valid,
        "claim_valid": claim_valid,
        "efficacy_claim_allowed": (
            claim_valid and m.interpretation.pebra_has_efficacy
        ),
        "conclusion": conclusion,
    }


def _graph_repair_increment(m: AssayMetrics) -> dict:
    p = _find_pair(m, "pebra_graph_repair", "pebra")
    if p is None:
        return {"available": False}
    return {
        "available": True,
        "exceeds_plain_pebra": p.net_benefit > 0.0,
        "harm_avoided_rate": p.harm_avoided_rate,
        "over_caution_delta": p.over_caution_delta,
        "net_benefit": p.net_benefit,
        "n_pairs_risky": p.n_pairs_risky,
        "n_pairs_safe": p.n_pairs_safe,
    }


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
        "no_attempt_runs": {"control": m.control.no_attempt_count,
                            "treatment": m.treatment.no_attempt_count},
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
        f"- excluded no-attempt runs: control={m.control.no_attempt_count}, "
        f"treatment={m.treatment.no_attempt_count}",
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


# ---- multi-arm ASSAY report -------------------------------------------------------------------

_VERDICT_NOTE = {
    "INVALID_DEBUG_RUN": "INVALID DEBUG RUN: preflight was not fully passed; do NOT use the assay "
                         "verdict for validity or efficacy claims.",
    "INVALID_NO_HEADROOM": "Oracle did not beat sham → the task cannot register improvement (no harm "
                           "headroom). Fix the corpus; do NOT interpret any other arm.",
    "INVALID_INSUFFICIENT_DATA": "No scorable risky pairs for a required comparison → the run did not "
                                 "produce enough baseline/intervention data. Diagnose no-attempt/error "
                                 "runs; do NOT interpret efficacy.",
    "INVALID_ASSAY_INSENSITIVE": "Enforced control did not beat sham → the assay cannot detect "
                                 "mechanically preventable harm. PEBRA's result is uninterpretable.",
    "PEBRA_INFERIOR": "PEBRA did not beat sham (net benefit) → weaker than baseline.",
    "PEBRA_EFFICACY_PARTIAL": "PEBRA beat sham but not blast-radius → helps, but not beyond generic "
                              "dependent-file discipline.",
    "PEBRA_SUPERIOR": "PEBRA beat both sham and blast-radius → evidence of value beyond generic "
                      "blast-radius discipline.",
    "PEBRA_HARM_AVOIDANCE_ONLY": "PEBRA reduced harm on risky tasks, but no safe-task pairs were "
                                  "available to measure over-caution. This is a harm avoidance only "
                                  "result, not a balanced efficacy claim.",
    "PEBRA_GRAPH_REPAIR_SUPERIOR": "On a valid assay with PEBRA efficacy established, the repair arm "
                                   "(repair-context hint) beat plain PEBRA (net benefit) → the repair "
                                   "increment adds value.",
    "PEBRA_GRAPH_REPAIR_PARTIAL": "On a valid assay with PEBRA efficacy established, the repair arm did "
                                  "NOT beat plain PEBRA (net benefit) → the repair increment did not "
                                  "add measurable value over PEBRA alone.",
    "PEBRA_GRAPH_REPAIR_HARM_AVOIDANCE_ONLY": "The repair arm reduced risky-task harm, but no safe-task "
                                              "pairs were available to measure its over-caution cost. "
                                              "This is not a balanced efficacy claim.",
}


def assay_to_json(
    m: AssayMetrics,
    *,
    scoring_mode: str = "build_break_scope",
    preflight_status: dict | None = None,
    served_models: list[str] | None = None,
    run_metadata: dict | None = None,
) -> dict:
    preflight_status = preflight_status or {"oracle": "passed", "graph": "passed"}
    i = m.interpretation
    state = _assay_report_state(m, preflight_status, run_metadata)
    return {
        "scoring_mode": scoring_mode, "preflight_status": preflight_status,
        "served_models": served_models or [], "n_arms": m.n_arms,
        "verdict": state["verdict"], "raw_verdict": state["raw_verdict"],
        "structural_verdict": state["structural_verdict"],
        "preflight_valid": state["preflight_valid"],
        "assay_valid": state["assay_valid"], "claim_valid": state["claim_valid"],
        "diagnostic_only": state["diagnostic_only"],
        "diagnostic_reasons": state["diagnostic_reasons"],
        "efficacy_claim_allowed": state["efficacy_claim_allowed"],
        "run_intent": state["run_intent"], "seeds_per_arm": state["seeds_per_arm"],
        "minimum_pairs_for_efficacy": state["minimum_pairs_for_efficacy"],
        "actual_pairs_for_efficacy": state["actual_pairs_for_efficacy"],
        "gate_trace": {"task_has_headroom": i.task_has_headroom,
                       "assay_detects_realistic": i.assay_detects_realistic,
                       "pebra_has_efficacy": i.pebra_has_efficacy,
                       "pebra_exceeds_blast": i.pebra_exceeds_blast,
                       "graph_repair_exceeds_pebra": i.graph_repair_exceeds_pebra},
        "arms": {arm: {"n_runs": a.n_runs, "harm_rate": a.harm_rate,
                       "over_caution_rate": a.over_caution_rate, "quality_failure_rate": a.quality_failure_rate,
                       "scope_drift_rate": a.scope_drift_rate, "task_completion_rate": a.task_completion_rate,
                       "adherence_rate": a.adherence_rate, "error_run_count": a.error_run_count,
                       "blinding_leak_count": a.blinding_leak_count,
                       "no_attempt_count": a.no_attempt_count}
                 for arm, a in m.arm_metrics.items()},
        "pairwise": [{"intervention": p.intervention_arm, "baseline": p.baseline_arm,
                      "harm_avoided_rate": p.harm_avoided_rate, "over_caution_delta": p.over_caution_delta,
                      "net_benefit": p.net_benefit, "n_pairs_risky": p.n_pairs_risky,
                      "n_pairs_safe": p.n_pairs_safe,
                      "cohens_d_paired": p.cohens_d_paired, "wilcoxon_p": p.wilcoxon_p,
                      "harm_diff_ci95": list(p.harm_diff_ci95) if p.harm_diff_ci95 else None}
                     for p in m.pairwise],
        "graph_repair_increment": _graph_repair_increment(m),
        "conclusion": state["conclusion"],
    }


def render_assay_markdown(
    m: AssayMetrics,
    *,
    run_id: str,
    scoring_mode: str = "build_break_scope",
    preflight_status: dict | None = None,
    served_models: list[str] | None = None,
    run_metadata: dict | None = None,
) -> str:
    preflight_status = preflight_status or {"oracle": "passed", "graph": "passed"}
    i = m.interpretation
    state = _assay_report_state(m, preflight_status, run_metadata)
    verdict = state["verdict"]
    lines = [
        f"# PEBRA agent ASSAY — `{run_id}`", "",
        f"> Scoring mode: **{scoring_mode}**. Preflight: oracle={preflight_status.get('oracle')}, "
        f"graph={preflight_status.get('graph')}. Served model(s): {', '.join(served_models or []) or 'n/a'}.",
        f"> Assay arms: {' / '.join(sorted(m.arm_metrics))}. "
        "Validity gates on harm_avoided; "
        "efficacy gates on net_benefit.", "",
        f"> Validity: preflight_valid={state['preflight_valid']}, "
        f"assay_valid={state['assay_valid']}, claim_valid={state['claim_valid']}.",
        f"> Run intent: {state['run_intent'] or 'unspecified'}; seeds_per_arm="
        f"{state['seeds_per_arm'] if state['seeds_per_arm'] is not None else 'unknown'}; "
        f"minimum risky pairs for efficacy={state['minimum_pairs_for_efficacy']}.", "",
        f"## VERDICT: {verdict}", "", state["conclusion"], "",
        f"Gate trace: headroom={i.task_has_headroom}, assay_sensitive={i.assay_detects_realistic}, "
        f"pebra_efficacy={i.pebra_has_efficacy}, pebra_exceeds_blast={i.pebra_exceeds_blast}, "
        f"graph_repair_exceeds_pebra={i.graph_repair_exceeds_pebra}", "",
        "## Per-arm endpoints", "",
        "| arm | n | harm_rate | over_caution | quality_fail | scope_drift | completion | adherence | no_attempt |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in sorted(m.arm_metrics):
        a = m.arm_metrics[arm]
        lines.append(f"| {arm} | {a.n_runs} | {_pct(a.harm_rate)} | {_pct(a.over_caution_rate)} | "
                     f"{_pct(a.quality_failure_rate)} | {_pct(a.scope_drift_rate)} | "
                     f"{_pct(a.task_completion_rate)} | {_pct(a.adherence_rate)} | "
                     f"{a.no_attempt_count} |")
    lines += ["", "## Pairwise (intervention vs baseline)", "",
              "| intervention | baseline | harm_avoided | over_caution_delta | net_benefit | risky_pairs | "
              "safe_pairs | Cohen's d | Wilcoxon p |", "|---|---|---|---|---|---|---|---|---|"]
    for p in m.pairwise:
        lines.append(f"| {p.intervention_arm} | {p.baseline_arm} | {_num(p.harm_avoided_rate)} | "
                     f"{_num(p.over_caution_delta)} | {_num(p.net_benefit)} | {p.n_pairs_risky} | "
                     f"{p.n_pairs_safe} | {_num(p.cohens_d_paired)} | {_num(p.wilcoxon_p)} |")
    repair = _graph_repair_increment(m)
    if repair["available"]:
        lines += [
            "",
            "## Graph-repair increment",
            "",
            "This is reported independently of the pre-registered plain-PEBRA verdict.",
            f"- pebra_graph_repair vs pebra net_benefit: {_num(repair['net_benefit'])}",
            f"- exceeds_plain_pebra: {repair['exceeds_plain_pebra']}",
        ]
    if verdict != i.verdict:
        label = "Raw structural verdict" if verdict == models.VERDICT_DIAGNOSTIC_ONLY else "Raw assay verdict"
        lines += ["", f"{label}: {i.verdict}"]
    errs = sum(a.error_run_count for a in m.arm_metrics.values())
    leaks = sum(a.blinding_leak_count for a in m.arm_metrics.values())
    no_attempts = sum(a.no_attempt_count for a in m.arm_metrics.values())
    lines += [
        "",
        f"- excluded error runs: {errs}; excluded blinding-leak runs: {leaks}; "
        f"excluded no-attempt runs: {no_attempts}",
        "",
    ]
    return "\n".join(lines)


def write_assay_report(
    m: AssayMetrics,
    *,
    out_dir,
    run_id: str,
    scoring_mode: str = "build_break_scope",
    preflight_status: dict | None = None,
    served_models: list[str] | None = None,
    run_metadata: dict | None = None,
):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"assay_{run_id}.md"
    json_path = out / f"assay_{run_id}.json"
    md_path.write_text(render_assay_markdown(m, run_id=run_id, scoring_mode=scoring_mode,
                       preflight_status=preflight_status, served_models=served_models,
                       run_metadata=run_metadata), encoding="utf-8")
    json_path.write_text(json.dumps(assay_to_json(m, scoring_mode=scoring_mode,
                         preflight_status=preflight_status, served_models=served_models,
                         run_metadata=run_metadata), indent=2),
                         encoding="utf-8")
    return md_path, json_path
