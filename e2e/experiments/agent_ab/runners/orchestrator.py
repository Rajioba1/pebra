"""Orchestrate the paired A/B run: gate -> pre-flight -> task x arm x seed -> score -> report.

Gated (run_gate) so it cannot run by accident. Runs BOTH pre-flights first (oracle labels + graph
freshness), then loops paired trials, writing outcomes incrementally, and finally aggregates + renders
the scorecard. Runs sequentially (one pair at a time). Crash-survivable: a re-run reloads
``outcomes.json`` and resumes, skipping (task, seed) pairs that already have BOTH arms recorded.

Invoked as: ``python -m e2e.experiments.agent_ab.runners.orchestrator --run-id <id> --mode pilot``
It does not run under ordinary CI — the gate stays shut. No pebra import (assess via cli_harness).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.metrics import oracle, scorecard
from e2e.experiments.agent_ab.models import ARM_CONTROL, ARM_TREATMENT, RunOutcome, TaskSpec
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import preflight, run_artifacts, run_gate, run_pair
from e2e.experiments.agent_ab.specimens import loader as specimen_loader
from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
_EVAL_DIR = Path(__file__).resolve().parents[1] / "specimens" / "csharp" / "corpus" / "evaluator_tests"
_ALLOW_UNVERIFIED_ENV = "E2E_AB_ALLOW_UNVERIFIED"


class ExperimentRunError(RuntimeError):
    """A subject run returned an error (e.g. a live-client auth/rate failure). We FAIL-FAST rather than
    score it: a systematic misconfiguration (bad API key) errors on the very first run, so stopping
    avoids silently scoring a whole batch of no-op error runs as a valid null result. The incremental
    resume means fixing the cause and re-running only redoes the aborted (and any unstarted) pair."""


def load_corpus() -> list[TaskSpec]:
    return specimen_loader.load_corpus()


def _scoring_mode(corpus: list[TaskSpec]) -> str:
    """Self-describe the artifact from the planned tasks' actual evaluator projects."""
    has_project = [
        bool(t.evaluator_test_project) or any(((_EVAL_DIR / t.task_id).rglob("*.csproj")))
        for t in corpus
    ]
    if has_project and all(has_project):
        return "build_test_scope"
    if any(has_project):
        return "mixed_build_test_scope"
    return "build_break_scope"


def _config() -> dict[str, Any]:
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    if os.environ.get("E2E_AB_MODEL"):
        cfg["subject"]["model"] = os.environ["E2E_AB_MODEL"]
    return cfg


def _plan(corpus: list[TaskSpec], task_ids: list[str], seeds: int) -> list[tuple[TaskSpec, int]]:
    by_id = {t.task_id: t for t in corpus}
    missing = sorted(t for t in task_ids if t not in by_id)
    if missing:
        raise ValueError(f"configured task(s) missing from corpus: {', '.join(missing)}")
    chosen = [by_id[t] for t in sorted(task_ids)]
    return [(spec, seed) for spec in chosen for seed in range(seeds)]


def _live_assess_fn(repo_path: Path, spec: TaskSpec) -> dict[str, Any]:
    """Live graph-preflight assess: use the task's oracle patch as the proposed change so the target
    symbol resolves, and return the RAW assess payload for the freshness/resolution check."""
    patch = preflight._oracle_patch_dir(spec) / f"{spec.task_id}.patch"  # noqa: SLF001 - shared e2e path rule
    proposed = patch.read_text(encoding="utf-8") if patch.exists() else ""
    target = spec.expected_edit_scope[0] if spec.expected_edit_scope else ""
    request = {
        "schema_version": "0.1", "task": f"assess {spec.task_id}", "repo_id": "ab_graph_preflight",
        "candidate_actions": [{"id": "gp1", "label": "graph preflight", "action_type": "edit",
                               "affected_symbols": [], "expected_files": [target] if target else [],
                               "proposed_patch": proposed}],
        "evidence": {"events": [], "p_success": 0.75, "immediate_benefit": 0.5, "review_cost": 0.1,
                     "criticality_stage": "C3", "criticality_value": 0.8,
                     "edit_confidence_factors": {"p_success": 0.75, "evidence_quality": 0.7,
                                                 "testability": 0.7, "reversibility": 0.7,
                                                 "source_reliability": 0.7, "scope_control": 0.7},
                     "benefit_delta_evidence": {"source_type": "projected",
                                                "future_change_exposure": 0.0, "deltas": {}}},
        "thresholds": {"max_expected_loss_without_human": 0.45},
    }
    import tempfile  # noqa: PLC0415
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(request, fh)
        req_path = fh.name
    try:
        return cli_harness.assess(req_path, repo_root=repo_path, db=repo_path.parent / "gp.db")
    finally:
        Path(req_path).unlink(missing_ok=True)


def _write_outcomes(path: Path, outcomes: list[RunOutcome], run_id: str) -> None:
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    run_artifacts.atomic_write_json(path, payload)


def _write_run_status(
    out_dir: Path,
    mode: str,
    phase: str,
    *,
    preflight_status: dict[str, str] | None = None,
    scoring_mode: str | None = None,
    served_models: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Additive observability artifact (out_dir/run_status.json) — lets the run observatory read the
    authoritative mode + coarse phase instead of guessing from artifact presence. NEVER touches
    outcomes.json (the crash-survivable resume file). Best-effort: a write failure must not abort a run."""
    try:
        payload: dict[str, Any] = {
            "run_id": out_dir.name,
            "mode": mode,
            "phase": phase,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if preflight_status is not None:
            payload["preflight_status"] = preflight_status
        if scoring_mode is not None:
            payload["scoring_mode"] = scoring_mode
        if served_models is not None:
            payload["served_models"] = served_models
        if error is not None:
            payload["error"] = error
        run_artifacts.atomic_write_json(
            out_dir / "run_status.json",
            payload,
        )
    except OSError:
        pass


def _outcome_from_dict(d: dict[str, Any]) -> RunOutcome:
    d = dict(d)
    if d.get("blinding_terms") is not None:
        d["blinding_terms"] = tuple(d["blinding_terms"])
    if d.get("served_models") is not None:
        d["served_models"] = tuple(d["served_models"])
    return RunOutcome(**d)


def _served_models(outcomes: list[RunOutcome]) -> list[str]:
    models: set[str] = set()
    for outcome in outcomes:
        models.update(outcome.served_models)
    return sorted(models)


def _load_existing_outcomes(path: Path) -> list[RunOutcome]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_outcome_from_dict(o) for o in payload.get("outcomes", [])]


def _completed_pairs(outcomes: list[RunOutcome]) -> set[tuple[str, int]]:
    """A (task_id, seed) is completed only when BOTH arms are present. Partial pairs are NOT completed
    (they are dropped and re-run) so the scorecard never contains an asymmetric pair."""
    arms: dict[tuple[str, int], set[str]] = {}
    for o in outcomes:
        arms.setdefault((o.task_id, o.seed), set()).add(o.arm)
    return {k for k, a in arms.items() if {ARM_CONTROL, ARM_TREATMENT} <= a}


def _completed_units(outcomes: list[RunOutcome], specs_by_id: dict[str, TaskSpec]) -> set[tuple[str, int]]:
    """Assay resume: a (task_id, seed) is complete only when ALL expected arms for that task's
    harm_label are present (risky=4 arms, safe=3). Partial units are dropped and re-run."""
    present: dict[tuple[str, int], set[str]] = {}
    for o in outcomes:
        present.setdefault((o.task_id, o.seed), set()).add(o.arm)
    done: set[tuple[str, int]] = set()
    for key, arms in present.items():
        spec = specs_by_id.get(key[0])
        expected = set(run_pair.arms_for(spec.harm_label)) if spec else {ARM_CONTROL, ARM_TREATMENT}
        if expected <= arms:
            done.add(key)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the blinded agent A/B experiment (gated).")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=["smoke", "pilot", "powered", "assay", "assay_js"],
                        default="pilot")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run deterministic preflights and exit before any subject/model calls.",
    )
    parser.add_argument("--skip-oracle-preflight", action="store_true")
    parser.add_argument("--skip-graph-preflight", action="store_true")
    args = parser.parse_args(argv)

    if not args.preflight_only:
        run_gate.check_gate()  # fail-closed before ANY clone / model call
    if args.preflight_only and (args.skip_oracle_preflight or args.skip_graph_preflight):
        raise ExperimentRunError("--preflight-only cannot be combined with preflight skip flags")
    if (args.skip_oracle_preflight or args.skip_graph_preflight) and os.environ.get(_ALLOW_UNVERIFIED_ENV) != "1":
        raise ExperimentRunError(
            f"preflight skip requested; set {_ALLOW_UNVERIFIED_ENV}=1 for an explicitly unverified debug run"
        )

    cfg = _config()
    mode = cfg[args.mode]
    is_assay = args.mode in {"assay", "assay_js"}
    out_dir = _AB_OUT / args.run_id
    corpus = load_corpus()
    plan = _plan(corpus, mode["tasks"], mode["seeds_per_arm"])
    planned_specs = list({spec.task_id: spec for spec, _seed in plan}.values())
    preflight.run_repo_identity_preflight(planned_specs, rs.source_repo_path())
    external = rs.prepare_external_repo()

    preflight_status = {
        "oracle": "skipped" if args.skip_oracle_preflight else "passed",
        "graph": "skipped" if args.skip_graph_preflight else "passed",
        "revise_safer": "skipped" if (args.skip_graph_preflight or not is_assay) else "passed",
    }
    scoring_mode = _scoring_mode(planned_specs)
    _write_run_status(out_dir, args.mode, "preflight",
                      preflight_status=preflight_status, scoring_mode=scoring_mode)

    try:
        if not args.skip_oracle_preflight:
            preflight.run_oracle_preflight(planned_specs, external, out_dir=out_dir)
        if not args.skip_graph_preflight:
            preflight.run_graph_preflight(planned_specs, external, out_dir=out_dir,
                                          assess_fn=_live_assess_fn,
                                          setup_graph_fn=lambda p: cli_harness.setup_graph(repo_root=p),
                                          node_count_fn=lambda p: cli_harness.graph_node_counts(repo_root=p))
            if is_assay:
                preflight.run_revise_safer_calibration(
                    planned_specs,
                    external,
                    out_dir=out_dir,
                    setup_graph_fn=lambda p: cli_harness.setup_graph(repo_root=p),
                )
    except Exception as exc:
        _write_run_status(out_dir, args.mode, "failed",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          error=f"{type(exc).__name__}: {exc}")
        raise

    if args.preflight_only:
        _write_run_status(out_dir, args.mode, "finished",
                          preflight_status=preflight_status, scoring_mode=scoring_mode)
        return 0

    try:
        _write_run_status(out_dir, args.mode, "running",
                          preflight_status=preflight_status, scoring_mode=scoring_mode)
        specs_by_id = {s.task_id: s for s in corpus}
        outcomes_path = out_dir / "outcomes.json"
        existing = _load_existing_outcomes(outcomes_path)
        completed = _completed_units(existing, specs_by_id) if is_assay else _completed_pairs(existing)
        # Keep only outcomes from fully-completed units; drop any partial unit so it is re-run cleanly.
        outcomes: list[RunOutcome] = [o for o in existing if (o.task_id, o.seed) in completed]
        for spec, seed in plan:
            if (spec.task_id, seed) in completed:
                continue  # resume: this unit already has all its arms recorded
            results = (run_pair.run_trial(spec, seed, args.run_id) if is_assay
                       else run_pair.run_pair(spec, seed, args.run_id))
            for res in results:
                if res.error:
                    raise ExperimentRunError(
                        f"{res.arm} run for {spec.task_id} seed {seed} errored: {res.error}. "
                        "Fix the cause (e.g. ANTHROPIC_API_KEY) and re-run — resume skips completed units."
                    )
            for res in results:
                outcomes.append(oracle.score_run(res, spec))
            _write_outcomes(outcomes_path, outcomes, args.run_id)  # incremental / crash-survivable

        served_models = _served_models(outcomes)
        if is_assay:
            assay = scorecard.aggregate_assay(outcomes, arms=list(run_pair.arms_for("risky")),
                                              bootstrap_seed=cfg.get("bootstrap_seed", 0))
            render_report.write_assay_report(assay, out_dir=out_dir / "reports", run_id=args.run_id,
                                             scoring_mode=scoring_mode,
                                             preflight_status=preflight_status,
                                             served_models=served_models)
        else:
            ab = scorecard.aggregate(outcomes, bootstrap_seed=cfg.get("bootstrap_seed", 0))
            render_report.write_report(ab, out_dir=out_dir / "reports", run_id=args.run_id,
                                       scoring_mode=scoring_mode,
                                       preflight_status=preflight_status,
                                       served_models=served_models)
        _write_run_status(out_dir, args.mode, "finished",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          served_models=served_models)
    except Exception as exc:
        _write_run_status(out_dir, args.mode, "failed",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          error=f"{type(exc).__name__}: {exc}")
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover - gated live entry point
    raise SystemExit(main())
