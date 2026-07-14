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
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.metrics import oracle, scorecard
from e2e.experiments.agent_ab.models import (
    ARM_CONTROL,
    ARM_SHAM,
    ARM_TREATMENT,
    MIN_PAIRS_FOR_EFFICACY,
    RunOutcome,
    TaskSpec,
)
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import preflight, run_artifacts, run_gate, run_pair, subject_protocol
from e2e.experiments.agent_ab.specimens import loader as specimen_loader
from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness, rca_probe

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
_RCA_IDENTITY_KEYS = (
    "status", "validation_mode", "version", "sha256", "source_revision",
    "required_sha256", "accepted_version", "required_source_revision",
)
_RUN_DESIGN_KEYS = ("experiment_design_sha256",)
_EVAL_DIR = Path(__file__).resolve().parents[1] / "specimens" / "csharp" / "corpus" / "evaluator_tests"
_ALLOW_UNVERIFIED_ENV = "E2E_AB_ALLOW_UNVERIFIED"
_TERMINAL_PHASES = frozenset({"finished", "failed", "insufficient_data", "no_headroom"})


class ExperimentRunError(RuntimeError):
    """A subject run returned an error (e.g. a live-client auth/rate failure). We FAIL-FAST rather than
    score it: a systematic misconfiguration (bad API key) errors on the very first run, so stopping
    avoids silently scoring a whole batch of no-op error runs as a valid null result. The incremental
    resume means fixing the cause and re-running only redoes the aborted (and any unstarted) pair."""


class ShamAdmissionError(ExperimentRunError):
    """A sham-stage stop with a machine-readable cause for the run observatory."""

    def __init__(self, message: str, *, phase: str, failure_kind: str) -> None:
        super().__init__(message)
        self.phase = phase
        self.failure_kind = failure_kind


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
    patch_files = sorted(preflight._patch_text_touched_files(proposed))  # noqa: SLF001
    expected_files = patch_files or ([target] if target else [])
    request = {
        "schema_version": "0.1", "task": f"assess {spec.task_id}", "repo_id": "ab_graph_preflight",
        "candidate_actions": [{"id": "gp1", "label": "graph preflight", "action_type": "edit",
                               "affected_symbols": [], "expected_files": expected_files,
                               "proposed_patch": proposed}],
        "evidence": {"events": [], "p_success": 0.75, "immediate_benefit": 0.5, "review_cost": 0.1,
                     "criticality_stage": "C3", "criticality_value": 0.8,
                     "edit_confidence_factors": {"p_success": 0.75, "evidence_quality": 0.7,
                                                 "testability": 0.7, "reversibility": 0.7,
                                                 "source_reliability": 0.7, "scope_control": 0.7},
                    },
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


def _write_outcomes(
    path: Path,
    outcomes: list[RunOutcome],
    run_id: str,
    *,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    if run_metadata is not None:
        payload["run_metadata"] = run_metadata
    run_artifacts.atomic_write_json(path, payload)


def _write_run_status(
    out_dir: Path,
    mode: str,
    phase: str,
    *,
    preflight_status: dict[str, str] | None = None,
    scoring_mode: str | None = None,
    served_models: list[str] | None = None,
    run_metadata: dict[str, Any] | None = None,
    error: str | None = None,
    failure_kind: str | None = None,
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
        if run_metadata is not None:
            payload["run_metadata"] = run_metadata
        if error is not None:
            payload["error"] = error
        if failure_kind is not None:
            payload["failure_kind"] = failure_kind
        attempts = 3 if phase in _TERMINAL_PHASES else 1
        for attempt in range(attempts):
            try:
                run_artifacts.atomic_write_json(out_dir / "run_status.json", payload)
                return
            except OSError:
                if attempt + 1 == attempts:
                    return
                time.sleep(0.05 * (attempt + 1))
    except (OSError, TypeError, ValueError):
        pass


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[4],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = proc.stdout.strip()
    return commit or None


def _assert_harness_clean(root: Path | None = None) -> str:
    """Return the harness HEAD only when the experiment code is a clean Git checkout."""
    repo = root or Path(__file__).resolve().parents[4]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise ExperimentRunError("cannot identify a clean harness Git checkout") from None
    commit = head.stdout.strip()
    if head.returncode != 0 or not commit:
        raise ExperimentRunError("cannot identify a clean harness Git checkout")
    if status.returncode != 0:
        raise ExperimentRunError("cannot verify that the harness Git checkout is clean")
    if status.stdout.strip():
        raise ExperimentRunError(
            "harness Git checkout has uncommitted changes; commit them before running the experiment"
        )
    return commit


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rca_metadata(cfg: dict[str, Any]) -> dict[str, object]:
    rca_pin = cfg.get("toolchain", {}).get("rca", {})
    return {
        **rca_probe.fingerprint(
            accepted_version=rca_pin.get("version"),
            required_source_revision=rca_pin.get("source_revision"),
        ),
        "accepted_version": rca_pin.get("version"),
        "required_source_revision": rca_pin.get("source_revision"),
    }


def _rca_identity(rca: dict[str, object]) -> tuple[object, ...]:
    return tuple(rca[key] for key in _RCA_IDENTITY_KEYS)


def _design_sha256(design: dict[str, Any]) -> str:
    canonical = json.dumps(design, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _experiment_design(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    planned_specs: list[TaskSpec],
    *,
    provider: str,
    model: str | None,
    source_head_sha: str | None = None,
    harness_commit: str | None = None,
) -> dict[str, Any]:
    is_assay = args.mode in {"assay", "assay_js"}
    specs = sorted(planned_specs, key=lambda spec: spec.task_id)
    protocol_hashes = {
        arm: _sha256_text(subject_protocol.protocol_for_arm(arm))
        for arm in run_pair.arms_for("risky")
    }
    return {
        "git_commit": harness_commit if harness_commit is not None else _git_commit(),
        "mode": args.mode,
        "mode_config": cfg[args.mode],
        "subject_config": cfg.get("subject", {}),
        "thresholds": cfg.get("thresholds", {}),
        "bootstrap_seed": cfg.get("bootstrap_seed", 0),
        "provider": provider,
        "model": model,
        "source_head_sha": source_head_sha,
        "execution": {
            "parallel_arms": os.environ.get("E2E_AB_PARALLEL_ARMS") == "1",
            "max_workers_env": os.environ.get("E2E_AB_MAX_WORKERS"),
            "prior_mode": os.environ.get("E2E_AB_PRIOR_MODE", "explicit"),
            "semantic_diff_env": os.environ.get("PEBRA_CODEGRAPH_SEMANTIC_DIFF"),
            "thinking_env": os.environ.get("E2E_AB_THINKING"),
            "human_approval_policy": os.environ.get(
                "E2E_AB_HUMAN_APPROVAL_POLICY", "always_approve"
            ),
        },
        "subject_prompt_template_sha256": _sha256_text(run_pair._SUBJECT_PROMPT),  # noqa: SLF001
        "protocol_hashes": protocol_hashes,
        "task_specs": [dataclasses.asdict(spec) for spec in specs],
        "arm_topology": {
            spec.task_id: list(
                run_pair.arms_for(spec.harm_label)
                if is_assay
                else (ARM_CONTROL, ARM_TREATMENT)
            )
            for spec in specs
        },
    }


def _run_metadata(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    planned_specs: list[TaskSpec] | None = None,
    *,
    source_head_sha: str | None = None,
    harness_commit: str | None = None,
) -> dict[str, Any]:
    provider = os.environ.get("E2E_AB_PROVIDER", "anthropic").strip().lower() or "anthropic"
    subject_cfg = cfg.get("subject", {})
    model = os.environ.get("E2E_AB_MODEL")
    if not model:
        model = "deepseek-v4-flash" if provider == "deepseek" else subject_cfg.get("model")
    thinking_enabled = run_pair._subject_thinking_enabled(provider)  # noqa: SLF001 - shared run policy
    seeds_per_arm = int(cfg[args.mode]["seeds_per_arm"])
    design = _experiment_design(
        args,
        cfg,
        planned_specs or [],
        provider=provider,
        model=model,
        source_head_sha=source_head_sha,
        harness_commit=harness_commit,
    )
    return {
        "git_commit": design["git_commit"],
        "mode": args.mode,
        "seeds_per_arm": seeds_per_arm,
        "minimum_pairs_for_efficacy": MIN_PAIRS_FOR_EFFICACY,
        "run_intent": (
            "diagnostic" if seeds_per_arm < MIN_PAIRS_FOR_EFFICACY else "efficacy"
        ),
        "provider": provider,
        "model": model,
        "thinking_mode": (
            "provider_default" if thinking_enabled is None
            else "enabled" if thinking_enabled else "disabled"
        ),
        "parallel_arms": os.environ.get("E2E_AB_PARALLEL_ARMS") == "1",
        "max_workers_env": os.environ.get("E2E_AB_MAX_WORKERS"),
        "prior_mode": os.environ.get("E2E_AB_PRIOR_MODE", "explicit"),
        "human_approval_policy": os.environ.get(
            "E2E_AB_HUMAN_APPROVAL_POLICY", "always_approve"
        ),
        "env": {
            "E2E_AB_PARALLEL_ARMS": os.environ.get("E2E_AB_PARALLEL_ARMS"),
            "E2E_AB_MAX_WORKERS": os.environ.get("E2E_AB_MAX_WORKERS"),
            "E2E_AB_MODEL": os.environ.get("E2E_AB_MODEL"),
            "E2E_AB_PRIOR_MODE": os.environ.get("E2E_AB_PRIOR_MODE"),
            "E2E_AB_PROVIDER": os.environ.get("E2E_AB_PROVIDER"),
            "E2E_AB_THINKING": os.environ.get("E2E_AB_THINKING"),
            "E2E_AB_HUMAN_APPROVAL_POLICY": os.environ.get(
                "E2E_AB_HUMAN_APPROVAL_POLICY"
            ),
            "PEBRA_CODEGRAPH_SEMANTIC_DIFF": os.environ.get("PEBRA_CODEGRAPH_SEMANTIC_DIFF"),
        },
        "subject_prompt_template_sha256": design["subject_prompt_template_sha256"],
        "protocol_file": subject_protocol.INSTRUCTION_REL_PATH,
        "rca": _rca_metadata(cfg),
        "protocol_hashes": design["protocol_hashes"],
        "experiment_design": design,
        "experiment_design_sha256": _design_sha256(design),
    }


def _assert_resume_rca_compatible(out_dir: Path, run_metadata: dict[str, Any]) -> None:
    """Never combine completed outcomes produced by different or unknown RCA binaries."""
    outcomes_path = out_dir / "outcomes.json"
    if not outcomes_path.exists():
        return
    try:
        prior = json.loads(outcomes_path.read_text(encoding="utf-8"))
        prior_rca = prior["run_metadata"]["rca"]
        current_rca = run_metadata["rca"]
        prior_identity = _rca_identity(prior_rca)
        current_identity = _rca_identity(current_rca)
    except (OSError, ValueError, KeyError, TypeError):
        raise ExperimentRunError(
            "cannot resume outcomes without a prior RCA fingerprint; use a fresh run-id"
        ) from None
    if prior_identity != current_identity:
        raise ExperimentRunError(
            "RCA fingerprint changed since this run-id produced outcomes; use a fresh run-id"
        )


def _assert_resume_design_compatible(out_dir: Path, run_metadata: dict[str, Any]) -> None:
    """Keep one run-id bound to one claim design; never mix diagnostic and efficacy rows."""
    outcomes_path = out_dir / "outcomes.json"
    if not outcomes_path.exists():
        return
    try:
        prior = json.loads(outcomes_path.read_text(encoding="utf-8"))["run_metadata"]
        prior_identity = tuple(prior[key] for key in _RUN_DESIGN_KEYS)
        current_identity = tuple(run_metadata[key] for key in _RUN_DESIGN_KEYS)
    except (OSError, ValueError, KeyError, TypeError):
        raise ExperimentRunError(
            "cannot resume outcomes without prior run-design metadata; use a fresh run-id"
        ) from None
    if prior_identity != current_identity:
        raise ExperimentRunError(
            "run design changed since this run-id produced outcomes; use a fresh run-id"
        )


def _assert_rca_probe_usable(run_metadata: dict[str, Any]) -> None:
    status = (run_metadata.get("rca") or {}).get("status")
    if status not in {"accepted", "rejected", "absent"}:
        raise ExperimentRunError(
            "RCA fingerprint probe failed; fix the binary/probe before starting or resuming this run"
        )


def _assert_active_rca_compatible(
    run_metadata: dict[str, Any], cfg: dict[str, Any]
) -> None:
    """Abort before persisting outcomes if RCA identity or acceptance changes mid-run."""
    current_rca = _rca_metadata(cfg)
    _assert_rca_probe_usable({"rca": current_rca})
    try:
        unchanged = _rca_identity(run_metadata["rca"]) == _rca_identity(current_rca)
    except (KeyError, TypeError):
        unchanged = False
    if not unchanged:
        raise ExperimentRunError(
            "RCA fingerprint changed during this run; outcomes were not persisted; use a fresh run-id"
        )


def _assert_active_harness_compatible(run_metadata: dict[str, Any]) -> None:
    """Abort before persistence if live CLI subprocesses could have loaded changed harness code."""
    try:
        expected = run_metadata["experiment_design"]["git_commit"]
    except (KeyError, TypeError):
        raise ExperimentRunError("active run is missing harness design identity") from None
    current = _assert_harness_clean()
    if current != expected:
        raise ExperimentRunError(
            "harness changed during this run; outcomes were not persisted; use a fresh run-id"
        )


def _outcome_from_dict(d: dict[str, Any]) -> RunOutcome:
    d = dict(d)
    if d.get("blinding_terms") is not None:
        d["blinding_terms"] = tuple(d["blinding_terms"])
    if d.get("served_models") is not None:
        d["served_models"] = tuple(d["served_models"])
    if d.get("prior_calibration_tags") is not None:
        d["prior_calibration_tags"] = tuple(d["prior_calibration_tags"])
    if d.get("graph_refinement_fact_kinds") is not None:
        d["graph_refinement_fact_kinds"] = tuple(d["graph_refinement_fact_kinds"])
    if d.get("graph_refinement_risk_probability_updates") is not None:
        d["graph_refinement_risk_probability_updates"] = tuple(
            {
                **update,
                "owner_node_ids": tuple(update.get("owner_node_ids") or ()),
            }
            for update in d["graph_refinement_risk_probability_updates"]
        )
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


def _sham_admission_failures(
    outcomes: list[RunOutcome], plan: list[tuple[TaskSpec, int]]
) -> tuple[list[str], list[str]]:
    """Require observed sham harm per risky task before spending on the remaining assay arms."""
    insufficient_data: list[str] = []
    no_headroom: list[str] = []
    risky_ids = sorted({spec.task_id for spec, _seed in plan if spec.harm_label == "risky"})
    for task_id in risky_ids:
        rows = [
            outcome for outcome in outcomes
            if outcome.task_id == task_id and outcome.arm == ARM_SHAM
        ]
        scorable = [
            outcome for outcome in rows
            if not outcome.error and not outcome.blinding_leak and not outcome.no_attempt
        ]
        harmed = sum(outcome.harm_materialized for outcome in scorable)
        excluded = len(rows) - len(scorable)
        if not scorable:
            insufficient_data.append(
                f"{task_id}: 0 scorable sham runs ({excluded} excluded of {len(rows)} total)"
            )
        elif harmed <= 0:
            no_headroom.append(
                f"{task_id}: {harmed}/{len(scorable)} scorable sham runs materialized harm "
                f"({excluded} excluded of {len(rows)} total)"
            )
    return insufficient_data, no_headroom


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
    harness_commit = _assert_harness_clean()

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
    preflight_status = {
        "oracle": "skipped" if args.skip_oracle_preflight else "pending",
        "graph": "skipped" if args.skip_graph_preflight else "pending",
        "revise_safer": "skipped" if (args.skip_graph_preflight or not is_assay) else "pending",
    }
    scoring_mode = _scoring_mode(planned_specs)
    source_root = rs.source_repo_path()
    preflight.run_repo_identity_preflight(planned_specs, source_root)
    external = rs.prepare_external_repo(source_root)
    run_metadata = _run_metadata(
        args,
        cfg,
        planned_specs,
        source_head_sha=getattr(external, "head_sha", None),
        harness_commit=harness_commit,
    )
    _assert_rca_probe_usable(run_metadata)
    _assert_resume_rca_compatible(out_dir, run_metadata)
    _assert_resume_design_compatible(out_dir, run_metadata)
    _write_run_status(out_dir, args.mode, "preflight",
                      preflight_status=preflight_status, scoring_mode=scoring_mode,
                      run_metadata=run_metadata)

    active_preflight: str | None = None
    try:
        if not args.skip_oracle_preflight:
            active_preflight = "oracle"
            preflight.run_oracle_preflight(planned_specs, external, out_dir=out_dir)
            preflight_status["oracle"] = "passed"
        if not args.skip_graph_preflight:
            active_preflight = "graph"
            preflight.run_graph_preflight(planned_specs, external, out_dir=out_dir,
                                          assess_fn=_live_assess_fn,
                                          setup_graph_fn=lambda p: cli_harness.setup_graph(repo_root=p),
                                          node_count_fn=lambda p: cli_harness.graph_node_counts(repo_root=p))
            preflight_status["graph"] = "passed"
            if is_assay:
                active_preflight = "revise_safer"
                preflight.run_revise_safer_calibration(
                    planned_specs,
                    external,
                    out_dir=out_dir,
                    setup_graph_fn=lambda p: cli_harness.setup_graph(repo_root=p),
                )
                preflight_status["revise_safer"] = "passed"
        active_preflight = None
    except Exception as exc:
        if active_preflight is not None:
            preflight_status[active_preflight] = "failed"
        _write_run_status(out_dir, args.mode, "failed",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          run_metadata=run_metadata,
                          error=f"{type(exc).__name__}: {exc}")
        raise

    if args.preflight_only:
        _write_run_status(out_dir, args.mode, "finished",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          run_metadata=run_metadata)
        return 0

    try:
        _write_run_status(out_dir, args.mode, "running",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          run_metadata=run_metadata)
        specs_by_id = {s.task_id: s for s in corpus}
        outcomes_path = out_dir / "outcomes.json"
        existing = _load_existing_outcomes(outcomes_path)
        completed = _completed_units(existing, specs_by_id) if is_assay else _completed_pairs(existing)
        # Keep fully-completed units. For the JS assay only, also preserve a completed sham-stage row so
        # the paid headroom check is reusable on resume and sham is not paid for twice.
        outcomes: list[RunOutcome] = [o for o in existing if (o.task_id, o.seed) in completed]
        if args.mode == "assay_js":
            planned = {(spec.task_id, seed): spec for spec, seed in plan}
            outcomes.extend(
                o for o in existing
                if (o.task_id, o.seed) not in completed
                and o.arm == ARM_SHAM
                and not o.error
                and not o.blinding_leak
                and not o.no_attempt
                and (spec := planned.get((o.task_id, o.seed))) is not None
                and spec.harm_label == "risky"
            )
            present_sham = {(o.task_id, o.seed) for o in outcomes if o.arm == ARM_SHAM}
            for spec, seed in plan:
                key = (spec.task_id, seed)
                if spec.harm_label != "risky" or key in completed or key in present_sham:
                    continue
                (subject,) = run_pair.run_trial(
                    spec, seed, args.run_id, arms=(ARM_SHAM,)
                )
                if subject.error:
                    raise ExperimentRunError(
                        f"sham run for {spec.task_id} seed {seed} errored: {subject.error}"
                    )
                outcomes.append(oracle.score_run(subject, spec))
                present_sham.add(key)
                _assert_active_harness_compatible(run_metadata)
                _assert_active_rca_compatible(run_metadata, cfg)
                _write_outcomes(outcomes_path, outcomes, args.run_id, run_metadata=run_metadata)
            insufficient_data, no_headroom = _sham_admission_failures(outcomes, plan)
            if insufficient_data:
                raise ShamAdmissionError(
                    "sham admission has insufficient data; full assay arms were not run: "
                    + "; ".join(insufficient_data),
                    phase="insufficient_data",
                    failure_kind="sham_no_scorable_runs",
                )
            if no_headroom:
                raise ShamAdmissionError(
                    "sham admission found no headroom; full assay arms were not run: "
                    + "; ".join(no_headroom),
                    phase="no_headroom",
                    failure_kind="sham_no_headroom",
                )
        for spec, seed in plan:
            if (spec.task_id, seed) in completed:
                continue  # resume: this unit already has all its arms recorded
            if is_assay:
                present_arms = {
                    o.arm for o in outcomes if (o.task_id, o.seed) == (spec.task_id, seed)
                }
                if args.mode == "assay_js":
                    missing_arms = tuple(
                        arm for arm in run_pair.arms_for(spec.harm_label) if arm not in present_arms
                    )
                    results = run_pair.run_trial(spec, seed, args.run_id, arms=missing_arms)
                else:
                    results = run_pair.run_trial(spec, seed, args.run_id)
            else:
                results = run_pair.run_pair(spec, seed, args.run_id)
            for res in results:
                if res.error:
                    raise ExperimentRunError(
                        f"{res.arm} run for {spec.task_id} seed {seed} errored: {res.error}. "
                        "Fix the cause (e.g. ANTHROPIC_API_KEY) and re-run — resume skips completed units."
                    )
            for res in results:
                outcomes.append(oracle.score_run(res, spec))
            _assert_active_harness_compatible(run_metadata)
            _assert_active_rca_compatible(run_metadata, cfg)
            _write_outcomes(
                outcomes_path, outcomes, args.run_id, run_metadata=run_metadata
            )  # incremental / crash-survivable

        served_models = _served_models(outcomes)
        if is_assay:
            assay = scorecard.aggregate_assay(outcomes, arms=list(run_pair.arms_for("risky")),
                                              bootstrap_seed=cfg.get("bootstrap_seed", 0))
            render_report.write_assay_report(assay, out_dir=out_dir / "reports", run_id=args.run_id,
                                             scoring_mode=scoring_mode,
                                             preflight_status=preflight_status,
                                             served_models=served_models,
                                             run_metadata=run_metadata)
        else:
            ab = scorecard.aggregate(outcomes, bootstrap_seed=cfg.get("bootstrap_seed", 0))
            render_report.write_report(ab, out_dir=out_dir / "reports", run_id=args.run_id,
                                       scoring_mode=scoring_mode,
                                       preflight_status=preflight_status,
                                       served_models=served_models)
        _write_run_status(out_dir, args.mode, "finished",
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          served_models=served_models, run_metadata=run_metadata)
    except Exception as exc:
        phase = exc.phase if isinstance(exc, ShamAdmissionError) else "failed"
        failure_kind = exc.failure_kind if isinstance(exc, ShamAdmissionError) else None
        _write_run_status(out_dir, args.mode, phase,
                          preflight_status=preflight_status, scoring_mode=scoring_mode,
                          run_metadata=run_metadata,
                          error=f"{type(exc).__name__}: {exc}",
                          failure_kind=failure_kind)
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover - gated live entry point
    raise SystemExit(main())
