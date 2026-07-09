"""Per-arm preparation that runs AFTER clone+setup but BEFORE the agent — the oracle-positive arm's
pre-applied correct fix.

The oracle_positive arm is the assay's ENDPOINT FLOOR / guaranteed-effect positive control: the clone
already holds the correct state (``git apply specimens/csharp/corpus/correct_fix_patches/<task>.patch``), so the endpoint
(harm / completion) can register an improvement over sham WITHOUT depending on the agent heeding advice —
the smoke measured heeded=0%, so an advisory oracle would null too. If even this pre-patched arm doesn't
beat sham, the task has no harm headroom (or the metric is broken) and no result is interpretable.

Pure subprocess (``git apply``); never imports pebra. The pre-patch MUST be applied before the baseline
build check so the (correct) baseline passes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from e2e.experiments.agent_ab.runners.run_pair import RunPairError

_CORRECT_PATCH_DIR = (
    Path(__file__).resolve().parents[1] / "specimens" / "csharp" / "corpus" / "correct_fix_patches"
)


def prepare_oracle_patch(repo_path: Path, task_id: str, *, patch_dir: Path | None = None) -> Path:
    """git-apply the known correct fix for ``task_id`` into ``repo_path`` (the oracle-positive pre-patch).
    Returns the applied patch path. Raises RunPairError if the patch is missing or does not apply cleanly
    — fail at setup time (before any LLM call), never silently."""
    patch = (patch_dir or _CORRECT_PATCH_DIR) / f"{task_id}.patch"
    if not patch.is_file():
        raise RunPairError(f"oracle arm: no correct-fix patch for {task_id} at {patch}")
    proc = subprocess.run(["git", "apply", str(patch)], cwd=str(repo_path),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RunPairError(
            f"oracle arm: correct-fix patch for {task_id} did not apply cleanly: {proc.stderr.strip()}"
        )
    return patch
