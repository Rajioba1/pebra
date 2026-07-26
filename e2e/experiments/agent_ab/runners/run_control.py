"""Thin single-arm wrapper (control). Uses the same gated Phase-G stop as run_pair."""

from __future__ import annotations

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import SubjectResult, TaskSpec
from e2e.experiments.agent_ab.runners import run_pair
from e2e.external.utils import repo_source as rs


def run_control(spec: TaskSpec, seed: int, run_id: str) -> SubjectResult:
    external = rs.prepare_external_repo()
    setup = run_pair.prepare_arm(
        external, spec, models.ARM_CONTROL, seed, run_id, slot_index=0
    )
    return run_pair._invoke_trial_setups([setup], spec, seed)[0]  # noqa: SLF001
