"""Fail-closed pre-flight gates run BEFORE any subject agent — the experiment's integrity checks.

Two gates:

1. ``run_oracle_preflight`` — validates the hidden labels are real. For each task it applies the
   ground-truth oracle patch to a fresh clone and asserts the build outcome matches
   ``oracle_build_must_fail`` (trap tasks must break; safe tasks must build). This is what catches a
   bogus trap (e.g. a "delete" that still compiles): the run cannot proceed on a wrong label.

2. ``run_graph_preflight`` — validates the TREATMENT intervention is REAL. It proves the target
   resolves against a FRESH CodeGraph and that graph-backed fields actually appear in the assess
   payload. Without this, a stale/missing graph would silently degrade treatment to ~control (untrusted
   evidence) and the null result would be an artifact, not a finding.

Both RAISE ``PreflightError`` on any mismatch. The build/setup-graph/assess calls are injectable so the
orchestration logic and the pure assertions are unit-testable without dotnet or the real repo. No pebra
import (graph/assess reached via cli_harness subprocess).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import repo_source as rs

_CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"
_PATCH_DIR = _CORPUS_DIR / "oracle_patches"

_TRUSTED_RESOLUTION = {"location", "name_fallback"}


class PreflightError(RuntimeError):
    """A pre-flight integrity gate failed; the experiment must not run."""


# ---- oracle-outcome preflight -----------------------------------------------------------------


def _apply_patch(patch_file: Path, repo_path: Path) -> None:
    proc = subprocess.run(["git", "apply", str(patch_file)], cwd=str(repo_path),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise PreflightError(f"git apply failed for {patch_file.name}: {proc.stderr.strip()}")


def _oracle_failure(spec: TaskSpec, build) -> str | None:
    """Pure: given a build result, return a failure message iff it contradicts the oracle label."""
    if not build.ran:
        return f"{spec.task_id}: build did not run (dotnet SDK absent?)"
    if spec.oracle_build_must_fail and build.passed:
        return f"{spec.task_id}: oracle says build MUST fail, but it PASSED (label is wrong)"
    if not spec.oracle_build_must_fail and not build.passed:
        return f"{spec.task_id}: oracle says build should pass, but it FAILED: {build.error_summary[:200]}"
    return None


def run_oracle_preflight(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    build_fn: Callable[[Path], Any] | None = None,
    patch_dir: Path | None = None,
) -> None:
    """Apply each task's oracle patch to a fresh clone and assert the build outcome matches the label."""
    build_fn = build_fn or dn.run_build
    patch_dir = patch_dir or _PATCH_DIR
    failures: list[str] = []
    for spec in corpus:
        # Accumulate ALL failures (missing patch / apply failure / label mismatch / infra) — never
        # first-fail — so one drifted patch does not hide the others.
        try:
            patch_file = patch_dir / f"{spec.task_id}.patch"
            if not patch_file.exists():
                failures.append(f"{spec.task_id}: missing oracle patch at {patch_file}")
                continue
            dest = out_dir / "preflight" / spec.task_id / "repo"
            repo_path = rs.clone_at_recorded_head(external, dest)
            _apply_patch(patch_file, repo_path)
            msg = _oracle_failure(spec, build_fn(repo_path))
            if msg:
                failures.append(msg)
        except PreflightError as exc:
            failures.append(f"{spec.task_id}: {exc.args[0] if exc.args else exc}")
        except Exception as exc:  # noqa: BLE001 - infra (clone/build) error, recorded not raised mid-loop
            failures.append(f"{spec.task_id}: infrastructure error: {type(exc).__name__}: {exc}")
    if failures:
        raise PreflightError("oracle pre-flight failed:\n" + "\n".join(failures))


# ---- graph-freshness / treatment-integrity preflight ------------------------------------------


def _graph_backed_failure(spec: TaskSpec, assess_payload: dict[str, Any]) -> str | None:
    """Pure: return a failure message unless the assess payload proves a FRESH, RESOLVED graph.

    Requires the symbol/file fan-in evidence to be fresh and resolved — i.e. PEBRA's advisory for this
    target was produced from real graph evidence, not degraded/untrusted fallback.

    RESIDUAL GAP (honest): these are PEBRA's SELF-REPORTED freshness/resolution fields. A truly
    independent check — e.g. asserting the indexed C# node count exceeds a floor to catch a graph that
    was 'freshly' built but picked up no nodes — is not possible today: the assess payload carries no
    repo-wide node count and there is no pebra CLI that reports one. If such a CLI is added, assert its
    node count here. We do NOT fabricate a count. The resolution guard is the partial mitigation."""
    scores = assess_payload.get("scores") or {}
    sse = scores.get("symbol_scope_evidence") or {}
    fanin = sse.get("symbol_fanin") or {}
    freshness = fanin.get("graph_freshness")
    resolution = fanin.get("resolution_method")
    if freshness != "fresh":
        return f"{spec.task_id}: graph not fresh (graph_freshness={freshness!r})"
    if resolution not in _TRUSTED_RESOLUTION:
        return f"{spec.task_id}: target did not resolve on the graph (resolution_method={resolution!r})"
    return None


def run_graph_preflight(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    assess_fn: Callable[[Path, TaskSpec], dict[str, Any]],
    setup_graph_fn: Callable[[Path], None] | None = None,
) -> None:
    """Prove each task's target resolves on a fresh CodeGraph and yields graph-backed assess evidence.

    ``assess_fn(repo_path, spec)`` returns the treatment assess payload for the task's target; injectable
    so this is unit-testable with a fake payload. ``setup_graph_fn`` indexes the clone (defaults to the
    cli_harness setup-graph in the live path)."""
    failures: list[str] = []
    for spec in corpus:
        if spec.harm_label != "risky":
            continue  # graph value is asserted on the risky (graph-dependent) targets
        # Accumulate ALL failures; a clone/setup-graph/assess infra error on one task is recorded as a
        # PreflightError line, not raised mid-loop as a raw CLIError that hides the other tasks.
        try:
            dest = out_dir / "graph_preflight" / spec.task_id / "repo"
            repo_path = rs.clone_at_recorded_head(external, dest)
            if setup_graph_fn is not None:
                setup_graph_fn(repo_path)
            msg = _graph_backed_failure(spec, assess_fn(repo_path, spec))
            if msg:
                failures.append(msg)
        except PreflightError as exc:
            failures.append(f"{spec.task_id}: {exc.args[0] if exc.args else exc}")
        except Exception as exc:  # noqa: BLE001 - infra error (clone/setup-graph/assess), recorded
            failures.append(f"{spec.task_id}: infrastructure error: {type(exc).__name__}: {exc}")
    if failures:
        raise PreflightError("graph pre-flight failed (treatment intervention not proven):\n"
                             + "\n".join(failures))
