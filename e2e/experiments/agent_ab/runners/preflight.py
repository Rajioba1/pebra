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
_CORRECT_PATCH_DIR = _CORPUS_DIR / "correct_fix_patches"

_TRUSTED_RESOLUTION = {"location"}

# Independent graph-validity floor: a freshly-built index that parsed no C# must NOT pass the graph
# preflight (self-reported freshness can't catch it). avalonia_template indexes ~700 C# callable nodes;
# 50 is a conservative floor that a real index clears easily but an empty/broken one cannot.
_MIN_CSHARP_NODES = 50


class PreflightError(RuntimeError):
    """A pre-flight integrity gate failed; the experiment must not run."""


def _live_node_counts(repo_path: Path) -> dict[str, Any]:
    """Live default for the graph node-count check: `pebra graph-stats --json` via cli_harness
    (subprocess, no pebra import). Injected with a fake in unit tests."""
    from e2e.utils import cli_harness  # noqa: PLC0415 - lazy; keeps unit tests import-light
    return cli_harness.graph_node_counts(repo_root=repo_path)


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


def _patch_touched_files(patch_file: Path) -> set[str]:
    touched: set[str] = set()
    for line in patch_file.read_text(encoding="utf-8").splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        for raw in parts[2:4]:
            path = raw[2:] if raw.startswith(("a/", "b/")) else raw
            if path != "/dev/null":
                touched.add(path.replace("\\", "/"))
    return touched


def _correct_fix_scope_failure(spec: TaskSpec, patch_file: Path) -> str | None:
    touched = _patch_touched_files(patch_file)
    expected = {p.replace("\\", "/").lstrip("./") for p in spec.expected_edit_scope}
    outside = sorted(touched - expected)
    if outside:
        return f"{spec.task_id}: correct-fix patch touches files outside expected scope: {', '.join(outside)}"
    if not touched:
        return f"{spec.task_id}: correct-fix patch touches no files"
    return None


def _correct_fix_failure(spec: TaskSpec, build) -> str | None:
    if not build.ran:
        return f"{spec.task_id}: correct-fix build did not run (dotnet SDK absent?)"
    if not build.passed:
        return f"{spec.task_id}: correct-fix patch should build, but FAILED: {build.error_summary[:200]}"
    return None


def run_oracle_preflight(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    build_fn: Callable[[Path], Any] | None = None,
    patch_dir: Path | None = None,
    correct_patch_dir: Path | None = None,
) -> None:
    """Apply each task's oracle patch to a fresh clone and assert the build outcome matches the label.

    Risky tasks also need a correct-fix reference patch. That patch must touch only the hidden
    expected scope and must build, proving the widened scope is complete enough to reward a safe fix
    rather than only rewarding refusal.
    """
    build_fn = build_fn or dn.run_build
    patch_dir = patch_dir or _PATCH_DIR
    correct_patch_dir = correct_patch_dir or _CORRECT_PATCH_DIR
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
            if spec.harm_label == "risky":
                correct_patch = correct_patch_dir / f"{spec.task_id}.patch"
                if not correct_patch.exists():
                    failures.append(f"{spec.task_id}: missing correct-fix patch at {correct_patch}")
                    continue
                scope_msg = _correct_fix_scope_failure(spec, correct_patch)
                if scope_msg:
                    failures.append(scope_msg)
                    continue
                correct_dest = out_dir / "preflight" / f"{spec.task_id}_correct" / "repo"
                correct_repo = rs.clone_at_recorded_head(external, correct_dest)
                _apply_patch(correct_patch, correct_repo)
                fix_msg = _correct_fix_failure(spec, build_fn(correct_repo))
                if fix_msg:
                    failures.append(fix_msg)
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

    These are PEBRA's self-reported freshness/resolution fields for THIS target. They are paired with an
    INDEPENDENT graph-validity check in ``run_graph_preflight`` — a repo-wide C# node-count floor (via
    ``pebra graph-stats``) — which catches a 'freshly' built index that actually parsed no nodes and
    could otherwise name-fallback-resolve while reporting fresh. Together: target resolves fresh AND the
    graph demonstrably contains real C# nodes."""
    scores = assess_payload.get("scores") or {}
    sse = scores.get("symbol_scope_evidence") or {}
    fanin = sse.get("symbol_fanin") or {}
    freshness = fanin.get("graph_freshness")
    resolution = fanin.get("resolution_method")
    if freshness != "fresh":
        return f"{spec.task_id}: graph not fresh (graph_freshness={freshness!r})"
    if resolution not in _TRUSTED_RESOLUTION:
        return (
            f"{spec.task_id}: target did not resolve by location on the graph "
            f"(resolution_method={resolution!r})"
        )
    reach = max(
        int(fanin.get("caller_count") or 0),
        int(fanin.get("modify_impact_count") or 0),
        int(fanin.get("modify_transitive_impact_count") or 0),
    )
    expected_loss = scores.get("expected_loss")
    if reach <= 0 or not isinstance(expected_loss, (int, float)) or expected_loss <= 0.0:
        return (
            f"{spec.task_id}: fresh graph resolved but did not produce material graph-backed risk "
            f"(reach={reach}, expected_loss={expected_loss!r})"
        )
    return None


def run_graph_preflight(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    assess_fn: Callable[[Path, TaskSpec], dict[str, Any]],
    setup_graph_fn: Callable[[Path], None] | None = None,
    node_count_fn: Callable[[Path], dict[str, Any]] | None = None,
) -> None:
    """Prove each task's target resolves on a fresh CodeGraph and yields graph-backed assess evidence.

    ``assess_fn(repo_path, spec)`` returns the treatment assess payload for the task's target; injectable
    so this is unit-testable with a fake payload. ``setup_graph_fn`` indexes the clone (defaults to the
    cli_harness setup-graph in the live path). ``node_count_fn`` returns repo-wide CodeGraph node counts
    (defaults to ``pebra graph-stats`` via cli_harness) for the INDEPENDENT validity check: it asserts
    the index actually contains C# callable nodes, catching a 'fresh' but empty index."""
    node_count_fn = node_count_fn or _live_node_counts
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
            # Independent validity FIRST: a 'fresh' index that parsed no C# is not a real intervention.
            counts = node_count_fn(repo_path)
            cs_nodes = int(counts.get("csharp_callable", 0))
            if cs_nodes < _MIN_CSHARP_NODES:
                failures.append(
                    f"{spec.task_id}: CodeGraph has {cs_nodes} C# callable nodes "
                    f"(< {_MIN_CSHARP_NODES}); index is empty/degraded despite freshness self-report"
                )
                continue
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
