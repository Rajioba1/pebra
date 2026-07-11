"""Pure(ish) aggregation for the run observatory: turn a run dir's artifacts into the /api JSON view.

I/O-light: it reads the run dir's files and calls the EXISTING e2e producers/aggregators. It does NOT
reimplement any scorecard/plan/arm-token/serialization logic — every such function is imported from its
owning module (drift is pinned by tests/... contract tests). It NEVER imports pebra and NEVER writes.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import launch_dashboard, orchestrator, run_pair

_ASSAY_MODES = frozenset({"assay", "assay_js"})
# Arms that run the REAL advisory backend against a pebra store (db=pebra.db) — i.e. the arms that leave
# a pebra.db to open in the real dashboard. Includes the legacy `treatment` arm (pilot is the default
# mode). Derived from run_pair so this can't drift from the runner's actual store-writing set.
_STORE_ARMS = tuple(sorted(run_pair._REAL_ADVISORY_ARMS))  # noqa: SLF001 - pinned by contract test
# Port used only to RENDER the copy-paste `pebra dashboard` command (v1). v2 spawn uses an OS-assigned
# port. Kept distinct from a spawned dashboard's own port to avoid operator confusion.
_DASHBOARD_PORT = 9473


class RunNotFound(Exception):
    """Requested run-id has no directory under the assay output root."""


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_outcomes(run_dir: Path) -> list[models.RunOutcome]:
    payload = _read_json(run_dir / "outcomes.json") or {}
    return [orchestrator._outcome_from_dict(o) for o in payload.get("outcomes", [])]  # noqa: SLF001


def _has_assay_arms(outcomes: list[models.RunOutcome]) -> bool:
    return any(o.arm not in (models.ARM_CONTROL, models.ARM_TREATMENT) for o in outcomes)


def _phase_and_mode(run_dir: Path, cli_mode: str | None) -> tuple[str, str | None, dict]:
    status = _read_json(run_dir / "run_status.json") or {}
    outcomes_payload = _read_json(run_dir / "outcomes.json") or {}
    outcomes_exists = (run_dir / "outcomes.json").exists()
    reports_dir = run_dir / "reports"
    reports_exist = reports_dir.is_dir() and any(reports_dir.glob("*.json"))
    if status.get("phase"):
        phase = status["phase"]
    elif reports_exist:
        phase = "finished"
    elif outcomes_exists:
        phase = "running"
    elif (run_dir / "preflight").exists():
        phase = "preflight"
    else:
        phase = "unknown"
    mode = status.get("mode") or cli_mode

    mtimes = [p.stat().st_mtime for p in (run_dir / "outcomes.json", run_dir / "run_status.json")
              if p.exists()]
    newest = max(mtimes) if mtimes else None
    detail = {
        "out_dir_exists": run_dir.is_dir(),
        "outcomes_exists": outcomes_exists,
        "reports_exist": reports_exist,
        "last_activity_iso": (datetime.fromtimestamp(newest, timezone.utc).isoformat()
                              if newest else None),
        "stale_seconds": (round(time.time() - newest) if newest else None),
    }
    for key in (
        "preflight_status", "served_models", "scoring_mode", "error", "failure_kind",
        "run_metadata",
    ):
        if key in status:
            detail[key] = status[key]
    if "run_metadata" not in detail and isinstance(outcomes_payload.get("run_metadata"), dict):
        detail["run_metadata"] = outcomes_payload["run_metadata"]
    return phase, mode, detail


def _task_meta(spec) -> dict:
    return {"language": spec.language, "specimen": spec.specimen, "harm_label": spec.harm_label}


def _planned_grid(mode: str | None, is_assay: bool, corpus: list, config: dict) -> dict | None:
    """The full (task_id, seed, arm) grid the run intends to fill, with task metadata.

    Returns None when the mode/plan can't be known. Assay modes fan out arms_for(harm_label);
    legacy modes are the fixed control/treatment pair.
    """
    mode_cfg = config.get(mode) if mode else None
    if not mode_cfg or not corpus:
        return None
    try:
        plan = orchestrator._plan(corpus, mode_cfg["tasks"], mode_cfg["seeds_per_arm"])  # noqa: SLF001
    except (KeyError, ValueError, TypeError):
        return None
    grid: dict[tuple[str, int, str], dict] = {}
    for spec, seed in plan:
        arms = run_pair.arms_for(spec.harm_label) if is_assay else (models.ARM_CONTROL,
                                                                     models.ARM_TREATMENT)
        for arm in arms:
            grid[(spec.task_id, seed, arm)] = _task_meta(spec)
    return grid


def _summary(o: models.RunOutcome) -> dict:
    return {"harm_materialized": o.harm_materialized, "task_completed": o.task_completed,
            "over_cautious": o.over_cautious, "blinding_leak": o.blinding_leak,
            "quality_failure": o.quality_failure, "error": o.error,
            "timed_out": o.timed_out,
            "limit_reason": o.limit_reason,
            "no_attempt": o.no_attempt,
            "advisory_called": o.advisory_called,
            "advisory_decision": o.advisory_decision,
            "over_caution_cause": o.over_caution_cause,
            "protocol_file_read": o.protocol_file_read}


def _matrix(outcomes: list[models.RunOutcome], planned: dict | None, task_meta: dict[str, dict]) -> list[dict]:
    observed = {(o.task_id, o.seed, o.arm): o for o in outcomes}
    keys = set(observed)
    if planned is not None:
        keys |= set(planned)
    cells = []
    for task_id, seed, arm in sorted(keys):
        o = observed.get((task_id, seed, arm))
        meta = (planned or {}).get((task_id, seed, arm)) or task_meta.get(task_id, {})
        cells.append({
            "task_id": task_id, "seed": seed, "arm": arm,
            "status": "done" if o is not None else "pending",
            "language": meta.get("language"),
            "specimen": meta.get("specimen"),
            "harm_label": o.harm_label if o is not None else meta.get("harm_label"),
            "outcome_summary": _summary(o) if o is not None else None,
        })
    return cells


def _group_counts(matrix: list[dict], key: str) -> dict[str, dict[str, int]]:
    groups: dict[str, dict[str, int]] = {}
    for cell in matrix:
        label = cell.get(key) or "unknown"
        row = groups.setdefault(label, {"done": 0, "pending": 0, "total_planned": 0})
        row["total_planned"] += 1
        row[cell["status"]] += 1
    return groups


def _latest_report_json(run_dir: Path, is_assay: bool) -> dict | None:
    reports_dir = run_dir / "reports"
    if not reports_dir.is_dir():
        return None
    prefix = "assay_" if is_assay else "ab_"
    candidates = sorted(reports_dir.glob(f"{prefix}*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _read_json(path)
        if isinstance(payload, dict) and payload:
            return payload
    return None


def _scoreboard(outcomes: list[models.RunOutcome], is_assay: bool, config: dict,
                run_dir: Path, phase_detail: dict) -> dict:
    report_payload = _latest_report_json(run_dir, is_assay)
    if report_payload is not None and not is_assay:
        return report_payload
    seed = config.get("bootstrap_seed", 0)
    preflight_status = phase_detail.get("preflight_status") or {
        "oracle": "unknown",
        "graph": "unknown",
        "revise_safer": "unknown",
    }
    served_models = phase_detail.get("served_models") or []
    scoring_mode = phase_detail.get("scoring_mode") or "live_partial"
    if is_assay:
        assay = scorecard.aggregate_assay(outcomes, arms=list(run_pair.arms_for("risky")),
                                          bootstrap_seed=seed)
        if report_payload is not None:
            scoring_mode = report_payload.get("scoring_mode", scoring_mode)
            if "preflight_status" not in phase_detail:
                preflight_status = report_payload.get("preflight_status", preflight_status)
            served_models = report_payload.get("served_models", served_models)
        current = render_report.assay_to_json(
            assay,
            scoring_mode=scoring_mode,
            preflight_status=preflight_status,
            served_models=served_models,
            run_metadata=phase_detail.get("run_metadata"),
        )
        return {**(report_payload or {}), **current}
    ab = scorecard.aggregate(outcomes, bootstrap_seed=seed)
    return render_report.to_json(ab, scoring_mode=scoring_mode,
                                 preflight_status=preflight_status,
                                 served_models=served_models)


def _dashboards(run_id: str, ab_out: Path, outcomes: list[models.RunOutcome]) -> list[dict]:
    token_to_arm = {run_pair._arm_token(arm, run_id): arm for arm in _STORE_ARMS}  # noqa: SLF001
    result = []
    for store in launch_dashboard.list_run_dbs(run_id, ab_out=ab_out):
        # clone dir is "<task>_seed<n>_<blinded-token>": match the exact trailing token segment.
        clone_token = store["clone"].rsplit("_", 1)[-1]
        arm = token_to_arm.get(clone_token)
        command = None
        if store["repo"]:
            command = launch_dashboard.render_command(
                launch_dashboard.dashboard_command(store["repo"], store["db"], _DASHBOARD_PORT))
        result.append({
            "clone": store["clone"], "db": store["db"], "repo": store["repo"],
            "arm": arm, "arm_matched": arm is not None, "launch_command": command,
        })
    return result


def _trace_summary(path: Path) -> dict | None:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    tool_calls = payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else []
    turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
    advisory_calls = [
        c for c in tool_calls
        if isinstance(c, dict) and c.get("name") == "advisory_check"
    ]
    write_calls = [
        c for c in tool_calls
        if isinstance(c, dict) and c.get("name") == "write_file"
    ]
    blocked_writes = [
        c for c in write_calls
        if isinstance(c, dict) and c.get("blocked") is True
    ]
    last_turn = turns[-1] if turns and isinstance(turns[-1], dict) else {}
    last_tool = tool_calls[-1] if tool_calls and isinstance(tool_calls[-1], dict) else {}
    return {
        "clone": path.parent.name,
        "task_id": payload.get("task_id"),
        "seed": payload.get("seed"),
        "arm": payload.get("arm"),
        "model": payload.get("model"),
        "timed_out": final.get("timed_out"),
        "limit_reason": final.get("limit_reason"),
        "error": final.get("error"),
        "final_stop_reason": final.get("final_stop_reason"),
        "turn_count": final.get("turn_count"),
        "duration_seconds": final.get("duration_seconds"),
        "protocol_file_read": final.get("protocol_file_read"),
        "served_models": final.get("served_models") or [],
        "modified_files": final.get("modified_files") or [],
        "tool_call_count": len(tool_calls),
        "advisory_count": len(advisory_calls),
        "write_count": len(write_calls),
        "blocked_write_count": len(blocked_writes),
        "last_turn_stop_reason": last_turn.get("stop_reason"),
        "last_turn_latency_seconds": last_turn.get("latency_seconds"),
        "last_tool_name": last_tool.get("name"),
        "last_tool_latency_seconds": last_tool.get("latency_seconds"),
        "advisory_decisions": [
            c.get("advisory_decision") for c in advisory_calls if c.get("advisory_decision")
        ],
    }


def _traces(run_dir: Path) -> list[dict]:
    traces: list[dict] = []
    for path in sorted(run_dir.glob("*_seed*_*/subject_trace.json")):
        summary = _trace_summary(path)
        if summary is not None:
            traces.append(summary)
    return traces


def _coverage(run_dir: Path) -> dict:
    data = _read_json(run_dir / "preflight" / "coverage.json")
    if not isinstance(data, dict) or "by_language" not in data:
        return {"available": False, "by_language": None,
                "reason": "no coverage.json (graph preflight not run, or a pre-v1.5 run)"}
    return {"available": True, "by_language": data["by_language"], "reason": None}


def build_run_view(run_id: str, *, ab_out: Path, mode: str | None = None,
                   corpus: list | None = None, config: dict | None = None) -> dict:
    """The full /api/run/<id> payload for one run, assembled read-only from its artifacts."""
    ab_out = Path(ab_out)
    # "." / ".." pass the bare run-id regex but resolve to ab_out itself / its parent — treat as absent
    # (defense-in-depth; the server/entry points also reject them before reaching here).
    if run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        raise RunNotFound(run_id)
    run_dir = ab_out / run_id
    if not run_dir.is_dir():
        raise RunNotFound(run_id)
    if corpus is None:
        corpus = orchestrator.load_corpus()
    if config is None:
        config = orchestrator._config()  # noqa: SLF001

    outcomes = _load_outcomes(run_dir)
    phase, resolved_mode, phase_detail = _phase_and_mode(run_dir, mode)
    is_assay = (resolved_mode in _ASSAY_MODES) if resolved_mode else _has_assay_arms(outcomes)
    planned = _planned_grid(resolved_mode, is_assay, corpus, config)
    task_meta = {spec.task_id: _task_meta(spec) for spec in corpus}

    observed_keys = {(o.task_id, o.seed, o.arm) for o in outcomes}
    total_planned = len(planned) if planned is not None else None
    pending = len(set(planned) - observed_keys) if planned is not None else None
    matrix = _matrix(outcomes, planned, task_meta)

    return {
        "run_id": run_id,
        "mode": resolved_mode,
        "phase": phase,
        "phase_detail": phase_detail,
        "counts": {"done": len(observed_keys), "pending": pending, "total_planned": total_planned},
        "scoreboard": _scoreboard(outcomes, is_assay, config, run_dir, phase_detail),
        "groups": {
            "by_language": _group_counts(matrix, "language"),
            "by_specimen": _group_counts(matrix, "specimen"),
        },
        "matrix": matrix,
        "coverage": _coverage(run_dir),
        "dashboards": _dashboards(run_id, ab_out, outcomes),
        "traces": _traces(run_dir),
    }


def list_runs(*, ab_out: Path) -> list[dict]:
    """Cheap run index: one row per run dir (no scorecard aggregation)."""
    ab_out = Path(ab_out)
    if not ab_out.is_dir():
        return []
    runs = []
    for run_dir in sorted(ab_out.iterdir()):
        if not run_dir.is_dir():
            continue
        phase, _mode, detail = _phase_and_mode(run_dir, None)
        runs.append({
            "run_id": run_dir.name,
            "phase": phase,
            "done_count": len(_load_outcomes(run_dir)),
            "last_activity_iso": detail["last_activity_iso"],
        })
    return runs
