"""Scaffold one paired trial (control + treatment) for a task/seed.

It prepares both arms identically and blinded: isolated clones at the same SHA, the SAME
``advisory_check`` tool name in both (only the backend differs), an identical task prompt with no arm
identifier, and a recorded baseline build. ``_invoke_subject_agent`` is GATED: it calls
``run_gate.check_gate()`` first and only then drives the (now-live, Phase G) ``AnthropicClient``. The
old ``NotImplementedError`` stop is gone, so the fail-closed run gate is the SOLE guard - nothing
in-tree sets E2E_AB_RUN, and the gate-pin test asserts it raises when the gate is shut.

Never mutates the source checkout (repo_source clones into gitignored e2e/out/). No ``import pebra``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import SubjectResult, TaskSpec
from e2e.experiments.agent_ab.tools import advisory_check_real, advisory_check_sham, advisory_contract
from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"
_MIN_CSHARP_NODES = 50


class RunPairError(RuntimeError):
    """The paired run cannot start because a clone/setup/build invariant failed."""

_SUBJECT_PROMPT = """You are a software developer working on a C# codebase.

Your task:
{task_description}

You have these tools: read_file, write_file, list_dir, search_grep, run_build, run_tests,
{advisory_name}.
All file paths you provide to tools must be repository-relative paths.
Before significant edits, draft the intended patch and call {advisory_name} with the target file,
change summary, and intended patch.
Complete the task, then stop."""


@dataclass
class ArmSetup:
    arm: str
    repo_path: Path
    advisory_backend: Callable[..., dict[str, Any]]   # bound to the isolated clone for treatment
    baseline_build: dn.DotNetBuildResult
    subject_prompt: str


def _advisory_backend(arm: str, repo_path: Path, db_path: Path) -> Callable[..., dict[str, Any]]:
    """Return the callable backing the SAME 'advisory_check' tool. Only the content differs by arm."""
    if arm == models.ARM_TREATMENT:
        return lambda payload: advisory_check_real.advise(payload, repo_root=repo_path, db=db_path)
    return lambda payload: advisory_check_sham.advise(payload)


def _build_subject_prompt(spec: TaskSpec, repo_path: Path) -> str:
    # Identical text for both arms: only the tool NAME appears, and it is the shared blinded name.
    return _SUBJECT_PROMPT.format(
        task_description=spec.description,
        repo_path=str(repo_path),
        advisory_name=advisory_contract.TOOL_NAME,
    )


def _arm_token(arm: str, run_id: str) -> str:
    """Opaque, deterministic per-arm directory token. The arm NAME must never appear in any path or
    text the subject can see (the prompt interpolates repo_path) - a bare 'treatment'/'control' in the
    path would unblind the trial. The arm is tracked in code (ArmSetup.arm), never on disk."""
    return hashlib.sha256(f"{arm}:{run_id}".encode()).hexdigest()[:12]


def _remove_stale_arm_clone(dest: Path) -> None:
    root = _AB_OUT.resolve()
    resolved = dest.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RunPairError(f"refusing to remove arm clone outside {root}: {resolved}") from exc
    if resolved.exists():
        shutil.rmtree(resolved)


def _validate_baseline(repo_path: Path, baseline) -> None:
    if not getattr(baseline, "available", False) or not getattr(baseline, "ran", False):
        raise RunPairError(f"baseline build did not run for {repo_path}: {baseline.error_summary}")
    if not getattr(baseline, "passed", False):
        raise RunPairError(f"baseline build failed for {repo_path}: {baseline.error_summary}")


def prepare_arm(external: rs.ExternalRepo, spec: TaskSpec, arm: str, seed: int, run_id: str) -> ArmSetup:
    """Clone an isolated worktree for one arm and prepare everything up to the agent call. No agent run."""
    # Arm-NEUTRAL path: an opaque hash token, not the arm name - so nothing the agent sees reveals its arm.
    dest = _AB_OUT / run_id / f"{spec.task_id}_seed{seed}_{_arm_token(arm, run_id)}" / "repo"
    _remove_stale_arm_clone(dest)
    repo_path = rs.clone_at_recorded_head(external, dest)
    cli_harness.setup_graph(repo_root=repo_path)
    if arm == models.ARM_TREATMENT:
        counts = cli_harness.graph_node_counts(repo_root=repo_path)
        if int(counts.get("csharp_callable", 0)) < _MIN_CSHARP_NODES:
            raise RunPairError(
                f"treatment arm CodeGraph has {counts.get('csharp_callable', 0)} C# callable nodes "
                f"(< {_MIN_CSHARP_NODES})"
            )
    db_path = dest.parent / "pebra.db"
    baseline = dn.run_build_delta(repo_path)
    _validate_baseline(repo_path, baseline)
    return ArmSetup(
        arm=arm,
        repo_path=repo_path,
        advisory_backend=_advisory_backend(arm, repo_path, db_path),
        baseline_build=baseline,
        subject_prompt=_build_subject_prompt(spec, repo_path),
    )


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def _load_config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _invoke_subject_agent(setup: ArmSetup, spec: TaskSpec, seed: int) -> SubjectResult:
    """Drive a real, blinded coding subagent through the instrumented tool boundary, then run the
    HIDDEN evaluator (inject tests post-agent, build + test) to fill the build/test outcome fields.

    Fail-closed: the run gate (E2E_AB_RUN=1 AND E2E_EXTERNAL=1 AND ANTHROPIC_API_KEY) is checked FIRST,
    and it is the SOLE guard - ``AnthropicClient.send`` is now live (Phase G), so nothing but the gate
    stands between this path and a real LLM call. Nothing in-tree opens the gate. Imports are inline to
    keep the foundation importable without the anthropic SDK."""
    from e2e.experiments.agent_ab.runners import agent_loop, evaluator, run_gate  # noqa: PLC0415
    from e2e.experiments.agent_ab.runners.model_client import AnthropicClient  # noqa: PLC0415
    import os  # noqa: PLC0415

    run_gate.check_gate()
    cfg = _load_config()["subject"]
    run_cfg = agent_loop.RunConfig(
        model=cfg["model"],
        max_tool_calls_per_run=cfg.get("max_tool_calls_per_run", 50),
        max_wall_seconds_per_run=cfg.get("max_wall_seconds_per_run", 600),
        max_output_tokens_per_turn=cfg.get("max_output_tokens_per_turn", 4096),
        tools=tuple(cfg.get("tools", ())),
    )
    client = AnthropicClient(model=run_cfg.model, api_key=os.environ["ANTHROPIC_API_KEY"])
    result = agent_loop.run(setup, spec, seed, client=client, config=run_cfg)

    # HIDDEN oracle: inject evaluator tests post-agent, then build + test.
    build, test, _injected = evaluator.run_evaluator(setup.repo_path, spec.task_id)
    return dataclasses.replace(
        result,
        build_ran=build.ran,
        build_passed=(build.passed if build.ran else None),
        build_error_summary=build.error_summary,
        test_ran=bool(test and test.ran),
        test_passed=(test.passed if (test and test.ran) else None),
    )


def run_pair(spec: TaskSpec, seed: int, run_id: str) -> tuple[SubjectResult, SubjectResult]:
    """Prepare both arms and run the paired trial through the gated subject-agent seam."""
    external = rs.prepare_external_repo()
    control = prepare_arm(external, spec, models.ARM_CONTROL, seed, run_id)
    treatment = prepare_arm(external, spec, models.ARM_TREATMENT, seed, run_id)
    return (
        _invoke_subject_agent(control, spec, seed),
        _invoke_subject_agent(treatment, spec, seed),
    )
