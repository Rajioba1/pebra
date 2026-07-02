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
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.corpus import loader
from e2e.experiments.agent_ab.metrics import oracle, scorecard
from e2e.experiments.agent_ab.models import ARM_CONTROL, ARM_TREATMENT, RunOutcome, TaskSpec
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import preflight, run_gate, run_pair
from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
_PATCH_DIR = Path(__file__).resolve().parents[1] / "corpus" / "oracle_patches"
_EVAL_DIR = Path(__file__).resolve().parents[1] / "corpus" / "evaluator_tests"


def _scoring_mode(corpus: list[TaskSpec]) -> str:
    """Self-describe the artifact: 'build_test_scope' iff any task ships an evaluator test project,
    else 'build_break_scope' (build-break + scope only). Honest labelling of what the run measured."""
    if any((_EVAL_DIR / t.task_id).is_dir() for t in corpus):
        return "build_test_scope"
    return "build_break_scope"


def _config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


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
    patch = (_PATCH_DIR / f"{spec.task_id}.patch")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _outcome_from_dict(d: dict[str, Any]) -> RunOutcome:
    d = dict(d)
    if d.get("blinding_terms") is not None:
        d["blinding_terms"] = tuple(d["blinding_terms"])
    return RunOutcome(**d)


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the blinded agent A/B experiment (gated).")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=["pilot", "powered"], default="pilot")
    parser.add_argument("--skip-oracle-preflight", action="store_true")
    parser.add_argument("--skip-graph-preflight", action="store_true")
    args = parser.parse_args(argv)

    run_gate.check_gate()  # fail-closed before ANY clone / model call

    cfg = _config()
    mode = cfg[args.mode]
    out_dir = _AB_OUT / args.run_id
    corpus = loader.load_corpus()
    external = rs.prepare_external_repo()

    if not args.skip_oracle_preflight:
        preflight.run_oracle_preflight(corpus, external, out_dir=out_dir)
    if not args.skip_graph_preflight:
        preflight.run_graph_preflight(corpus, external, out_dir=out_dir,
                                      assess_fn=_live_assess_fn,
                                      setup_graph_fn=lambda p: cli_harness.setup_graph(repo_root=p))

    outcomes_path = out_dir / "outcomes.json"
    completed = _completed_pairs(_load_existing_outcomes(outcomes_path))
    # Keep only outcomes from fully-completed pairs; drop any partial pair so it is re-run cleanly.
    outcomes: list[RunOutcome] = [
        o for o in _load_existing_outcomes(outcomes_path) if (o.task_id, o.seed) in completed
    ]
    plan = _plan(corpus, mode["tasks"], mode["seeds_per_arm"])
    for spec, seed in plan:
        if (spec.task_id, seed) in completed:
            continue  # resume: this pair already has both arms recorded
        control, treatment = run_pair.run_pair(spec, seed, args.run_id)
        outcomes.append(oracle.score_run(control, spec))
        outcomes.append(oracle.score_run(treatment, spec))
        _write_outcomes(outcomes_path, outcomes, args.run_id)  # incremental / crash-survivable

    ab = scorecard.aggregate(outcomes, bootstrap_seed=cfg.get("bootstrap_seed", 0))
    planned_specs = list({spec.task_id: spec for spec, _seed in plan}.values())
    render_report.write_report(ab, out_dir=out_dir / "reports", run_id=args.run_id,
                               scoring_mode=_scoring_mode(planned_specs))
    return 0


if __name__ == "__main__":  # pragma: no cover - gated live entry point
    raise SystemExit(main())
