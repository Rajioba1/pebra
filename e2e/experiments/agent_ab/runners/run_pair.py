"""Scaffold one paired trial (control + treatment) for a task/seed — UP TO the real-agent invocation.

It prepares both arms identically and blinded: isolated clones at the same SHA, the SAME
``advisory_check`` tool name in both (only the backend differs), an identical task prompt with no arm
identifier, and a recorded baseline build. It then calls ``_invoke_subject_agent`` — the UNIMPLEMENTED
STOP SEAM. Nothing here spawns an agent or calls an LLM.

Never mutates the source checkout (repo_source clones into gitignored e2e/out/). No ``import pebra``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import SubjectResult, TaskSpec
from e2e.experiments.agent_ab.tools import advisory_check_real, advisory_check_sham, advisory_contract
from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import repo_source as rs

_AB_OUT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab"

_SUBJECT_PROMPT = """You are a software developer working on a C# codebase.

Your task:
{task_description}

The repository is at: {repo_path}

You have these tools: read_file, write_file, run_build, run_tests, {advisory_name}.
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
    text the subject can see (the prompt interpolates repo_path) — a bare 'treatment'/'control' in the
    path would unblind the trial. The arm is tracked in code (ArmSetup.arm), never on disk."""
    return hashlib.sha256(f"{arm}:{run_id}".encode()).hexdigest()[:12]


def prepare_arm(external: rs.ExternalRepo, spec: TaskSpec, arm: str, seed: int, run_id: str) -> ArmSetup:
    """Clone an isolated worktree for one arm and prepare everything up to the agent call. No agent run."""
    # Arm-NEUTRAL path: an opaque hash token, not the arm name — so nothing the agent sees reveals its arm.
    dest = _AB_OUT / run_id / f"{spec.task_id}_seed{seed}_{_arm_token(arm, run_id)}" / "repo"
    repo_path = rs.clone_at_recorded_head(external, dest)
    db_path = dest.parent / "pebra.db"
    baseline = dn.run_build_delta(repo_path)
    return ArmSetup(
        arm=arm,
        repo_path=repo_path,
        advisory_backend=_advisory_backend(arm, repo_path, db_path),
        baseline_build=baseline,
        subject_prompt=_build_subject_prompt(spec, repo_path),
    )


def _invoke_subject_agent(setup: ArmSetup, spec: TaskSpec, seed: int) -> SubjectResult:
    """STOP SEAM — the ratified NEXT slice. Drives a real, blinded coding subagent through the
    instrumented tool boundary (read/write/build/tests/advisory_check), capturing transcript, tool
    calls, and diff into a SubjectResult. Deliberately unimplemented so the foundation can be reviewed
    before any agent or LLM is ever run."""
    raise NotImplementedError(
        "real-agent execution is the ratified next slice — do not run until approved"
    )


def run_pair(spec: TaskSpec, seed: int, run_id: str) -> tuple[SubjectResult, SubjectResult]:
    """Prepare both arms and (would) run the paired trial. Raises at the seam until the runner exists."""
    external = rs.prepare_external_repo()
    control = prepare_arm(external, spec, models.ARM_CONTROL, seed, run_id)
    treatment = prepare_arm(external, spec, models.ARM_TREATMENT, seed, run_id)
    return (
        _invoke_subject_agent(control, spec, seed),
        _invoke_subject_agent(treatment, spec, seed),
    )
