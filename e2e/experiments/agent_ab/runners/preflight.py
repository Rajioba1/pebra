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

import json
import math
import os
import subprocess
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab import backends
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import run_artifacts
from e2e.experiments.agent_ab.tools import candidate_materializer, candidate_verifier
from e2e.external.utils import repo_source as rs

_CORPUS_DIR = Path(__file__).resolve().parents[1] / "specimens" / "csharp" / "corpus"
_PATCH_DIR = _CORPUS_DIR / "oracle_patches"
_CORRECT_PATCH_DIR = _CORPUS_DIR / "correct_fix_patches"

_TRUSTED_RESOLUTION = {"location"}

# Independent graph-validity floor: a freshly-built index that parsed no C# must NOT pass the graph
# preflight (self-reported freshness can't catch it). avalonia_template indexes ~700 C# callable nodes;
# 50 is a conservative floor that a real index clears easily but an empty/broken one cannot.
_MIN_CSHARP_NODES = 50
_REPO_ENV_VAR = "E2E_TEMPLATE_BLUEPRINT_REPO"
_BLOCKING_DECISIONS = {"reject", "ask_human", "revise_safer"}
_LANGUAGE_TIER_RANK = {"risk_only": 1, "partial": 2, "full": 3}


class PreflightError(RuntimeError):
    """A pre-flight integrity gate failed; the experiment must not run."""


def _corpus_dir(spec: TaskSpec) -> Path:
    specimen = spec.specimen or "csharp"
    return Path(__file__).resolve().parents[1] / "specimens" / specimen / "corpus"


def _oracle_patch_dir(spec: TaskSpec) -> Path:
    return _corpus_dir(spec) / "oracle_patches"


def _correct_patch_dir(spec: TaskSpec) -> Path:
    return _corpus_dir(spec) / "correct_fix_patches"


def _live_node_counts(repo_path: Path) -> dict[str, Any]:
    """Live default for the graph node-count check: `pebra graph-stats --json` via cli_harness
    (subprocess, no pebra import). Injected with a fake in unit tests."""
    from e2e.utils import cli_harness  # noqa: PLC0415 - lazy; keeps unit tests import-light
    return cli_harness.graph_node_counts(repo_root=repo_path)


def _live_language_capabilities(repo_path: Path) -> dict[str, Any]:
    """Live default for repo-level language capability checks: `pebra capabilities --json`."""
    from e2e.utils import cli_harness  # noqa: PLC0415 - lazy; keeps unit tests import-light
    return cli_harness.capabilities(repo_root=repo_path)


# ---- oracle-outcome preflight -----------------------------------------------------------------


def _apply_patch(patch_file: Path, repo_path: Path) -> None:
    proc = subprocess.run(["git", "apply", str(patch_file)], cwd=str(repo_path),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise PreflightError(f"git apply failed for {patch_file.name}: {proc.stderr.strip()}")


def _rmtree_onerror(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc_info[1].with_traceback(exc_info[2])


def _clone_fresh(external: rs.ExternalRepo, dest: Path, *, out_dir: Path) -> Path:
    root = out_dir.resolve()
    target = dest.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PreflightError(f"refusing to remove preflight clone outside {root}: {target}") from exc
    if target.exists():
        shutil.rmtree(target, onerror=_rmtree_onerror)
    return rs.clone_at_recorded_head(external, dest)


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


def run_repo_identity_preflight(planned_specs: list[TaskSpec], source_root: Path | str | None) -> None:
    """Fail before cloning if the configured source checkout is not the specimen required by the plan."""
    root = Path(source_root).resolve() if source_root is not None else None
    if root is None:
        raise PreflightError(f"repo identity pre-flight failed: set {_REPO_ENV_VAR}=<source repo>")
    specimens = {spec.specimen for spec in planned_specs}
    if len(specimens) > 1:
        raise PreflightError(
            "repo identity pre-flight failed: plan spans multiple repositories/specimens "
            f"({', '.join(sorted(specimens))}); split the run by specimen"
        )
    failures: list[str] = []
    for spec in planned_specs:
        required = [*spec.repo_identity_files, *spec.expected_edit_scope]
        for rel in required:
            if not (root / rel).exists():
                failures.append(
                    f"{spec.task_id}: missing {rel!r} under {_REPO_ENV_VAR}={root}"
                )
    if failures:
        raise PreflightError("repo identity pre-flight failed:\n" + "\n".join(failures))


def _run_spec_test(spec: TaskSpec, repo_path: Path, test_fn: Callable[..., Any]):
    if not spec.evaluator_test_project:
        return None
    project = (repo_path / spec.evaluator_test_project).resolve()
    if test_fn is not None:
        if spec.evaluator_test_filter:
            return test_fn(repo_path, project=project, test_filter=spec.evaluator_test_filter)
        return test_fn(repo_path, project=project)
    backend = backends.backend_for_spec(spec)
    return backend.run_tests(repo_path, spec, project=project, test_filter=spec.evaluator_test_filter)


def _run_spec_build(spec: TaskSpec, repo_path: Path, build_fn: Callable[[Path], Any] | None):
    if build_fn is not None:
        return build_fn(repo_path)
    return backends.backend_for_spec(spec).run_build(repo_path, spec)


def _zero_selected_tests_failure(spec: TaskSpec, test, label: str) -> str | None:
    if spec.harm_type != "test_failure":
        return None
    if getattr(test, "tests_selected", None) == 0:
        return f"{spec.task_id}: {label} selected zero tests"
    return None


def _oracle_test_failure(spec: TaskSpec, test) -> str | None:
    if spec.harm_type != "test_failure":
        return None
    if test is None or not test.ran:
        return f"{spec.task_id}: oracle test did not run (dotnet SDK absent?)"
    zero_msg = _zero_selected_tests_failure(spec, test, "oracle filtered test")
    if zero_msg:
        return zero_msg
    if test.passed:
        return f"{spec.task_id}: oracle says filtered test MUST fail, but it PASSED"
    return None


def _correct_fix_test_failure(spec: TaskSpec, test) -> str | None:
    if spec.harm_type != "test_failure":
        return None
    if test is None or not test.ran:
        return f"{spec.task_id}: correct-fix test did not run (dotnet SDK absent?)"
    zero_msg = _zero_selected_tests_failure(spec, test, "correct-fix filtered test")
    if zero_msg:
        return zero_msg
    if not test.passed:
        return f"{spec.task_id}: correct-fix filtered test should pass, but FAILED: {test.error_summary[:200]}"
    return None


def run_oracle_preflight(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    build_fn: Callable[[Path], Any] | None = None,
    test_fn: Callable[..., Any] | None = None,
    patch_dir: Path | None = None,
    correct_patch_dir: Path | None = None,
) -> None:
    """Apply each task's oracle patch to a fresh clone and assert the build outcome matches the label.

    Risky tasks also need a correct-fix reference patch. That patch must touch only the hidden
    expected scope and must build, proving the widened scope is complete enough to reward a safe fix
    rather than only rewarding refusal.
    """
    failures: list[str] = []
    for spec in corpus:
        # Accumulate ALL failures (missing patch / apply failure / label mismatch / infra) — never
        # first-fail — so one drifted patch does not hide the others.
        try:
            spec_patch_dir = patch_dir or _oracle_patch_dir(spec)
            patch_file = spec_patch_dir / f"{spec.task_id}.patch"
            if not patch_file.exists():
                failures.append(f"{spec.task_id}: missing oracle patch at {patch_file}")
                continue
            dest = out_dir / "preflight" / spec.task_id / "repo"
            repo_path = _clone_fresh(external, dest, out_dir=out_dir)
            _apply_patch(patch_file, repo_path)
            build = _run_spec_build(spec, repo_path, build_fn)
            msg = _oracle_failure(spec, build)
            if msg:
                failures.append(msg)
            elif spec.harm_type == "test_failure":
                test_msg = _oracle_test_failure(spec, _run_spec_test(spec, repo_path, test_fn))
                if test_msg:
                    failures.append(test_msg)
            if spec.harm_label == "risky":
                spec_correct_patch_dir = correct_patch_dir or _correct_patch_dir(spec)
                correct_patch = spec_correct_patch_dir / f"{spec.task_id}.patch"
                if not correct_patch.exists():
                    failures.append(f"{spec.task_id}: missing correct-fix patch at {correct_patch}")
                    continue
                scope_msg = _correct_fix_scope_failure(spec, correct_patch)
                if scope_msg:
                    failures.append(scope_msg)
                    continue
                correct_dest = out_dir / "preflight" / f"{spec.task_id}_correct" / "repo"
                correct_repo = _clone_fresh(external, correct_dest, out_dir=out_dir)
                _apply_patch(correct_patch, correct_repo)
                fix_build = _run_spec_build(spec, correct_repo, build_fn)
                fix_msg = _correct_fix_failure(spec, fix_build)
                if fix_msg:
                    failures.append(fix_msg)
                elif spec.harm_type == "test_failure":
                    fix_test_msg = _correct_fix_test_failure(
                        spec, _run_spec_test(spec, correct_repo, test_fn)
                    )
                    if fix_test_msg:
                        failures.append(fix_test_msg)
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
    INDEPENDENT graph-validity check in ``run_graph_preflight`` — a C# node-count floor for legacy C#
    specimens, or the measured language's node_count for explicit multi-language tier floors — which
    catches a 'freshly' built index that actually parsed no nodes and could otherwise
    name-fallback-resolve while reporting fresh. Together: target resolves fresh AND the graph
    demonstrably contains real nodes for the task's required language."""
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
    required_tier = spec.required_language_tier
    if required_tier:
        graph_prov = assess_payload.get("graph_provenance") or {}
        cap = graph_prov.get("language_capability") or {}
        measured_tier = cap.get("tier")
        if _LANGUAGE_TIER_RANK.get(str(measured_tier), 0) < _LANGUAGE_TIER_RANK[required_tier]:
            return (
                f"{spec.task_id}: requires language tier {required_tier}, "
                f"but assess payload proved {measured_tier!r}"
            )
    if spec.requires_measured_benefit:
        breakdown = scores.get("benefit_breakdown") or {}
        source_type = breakdown.get("source_type")
        if source_type != "measured":
            return (
                f"{spec.task_id}: requires measured benefit evidence, "
                f"but assess payload proved {source_type!r}; install/configure RCA before this run"
            )
    return None


def _language_capability_from_payload(assess_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not assess_payload:
        return {}
    graph_prov = assess_payload.get("graph_provenance") or {}
    cap = graph_prov.get("language_capability") or {}
    return cap if isinstance(cap, dict) else {}


def _node_count_failure(
    spec: TaskSpec,
    counts: dict[str, Any],
    assess_payload: dict[str, Any] | None = None,
    capability: dict[str, Any] | None = None,
) -> str | None:
    cap = capability or _language_capability_from_payload(assess_payload)
    language = str(cap.get("language") or "").lower()
    if spec.required_language_tier and language and language != "csharp":
        lang_nodes = int(cap.get("node_count") or 0)
        if lang_nodes <= 0:
            return (
                f"{spec.task_id}: CodeGraph has {lang_nodes} {language} callable nodes; "
                "index is empty/degraded for the required language"
            )
        measured_tier = cap.get("tier")
        if _LANGUAGE_TIER_RANK.get(str(measured_tier), 0) < _LANGUAGE_TIER_RANK[spec.required_language_tier]:
            return (
                f"{spec.task_id}: requires language tier {spec.required_language_tier}, "
                f"but capability probe proved {measured_tier!r}"
            )
        return None
    cs_nodes = int(counts.get("csharp_callable", 0))
    if cs_nodes < _MIN_CSHARP_NODES:
        return (
            f"{spec.task_id}: CodeGraph has {cs_nodes} C# callable nodes "
            f"(< {_MIN_CSHARP_NODES}); index is empty/degraded despite freshness self-report"
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
    capability_fn: Callable[[Path], dict[str, Any]] | None = None,
) -> None:
    """Prove each task's target resolves on a fresh CodeGraph and yields graph-backed assess evidence.

    ``assess_fn(repo_path, spec)`` returns the treatment assess payload for the task's target; injectable
    so this is unit-testable with a fake payload. ``setup_graph_fn`` indexes the clone (defaults to the
    cli_harness setup-graph in the live path). ``node_count_fn`` returns repo-wide CodeGraph node counts
    (defaults to ``pebra graph-stats`` via cli_harness) for the INDEPENDENT validity check: legacy C#
    tasks require C# callable nodes, while explicit multi-language tier floors use the assessed
    language capability's node_count."""
    node_count_fn = node_count_fn or _live_node_counts
    capability_fn = capability_fn or _live_language_capabilities
    failures: list[str] = []
    coverage_by_language: dict[str, dict[str, Any]] = {}

    def _record_coverage(payload: dict[str, Any] | None) -> None:
        cap = _language_capability_from_payload(payload)
        _record_capability(cap)

    def _record_capability(cap: dict[str, Any]) -> None:
        lang = str(cap.get("language") or "").lower()
        if lang:  # record what was measured (even on tier/node failures — the coverage IS real)
            coverage_by_language[lang] = {"tier": cap.get("tier"), "node_count": cap.get("node_count")}

    def _capability_for_spec(repo_path: Path, spec: TaskSpec) -> dict[str, Any]:
        if not spec.required_language_tier:
            return {}
        try:
            payload = capability_fn(repo_path)
        except Exception:  # noqa: BLE001 - graph preflight records the tier failure below
            return {}
        measured = payload.get("measured") if isinstance(payload, dict) else None
        if not isinstance(measured, list):
            return {}
        wanted = spec.language.lower()
        for row in measured:
            if isinstance(row, dict) and str(row.get("language") or "").lower() == wanted:
                return row
        return {}

    for spec in corpus:
        if spec.harm_label != "risky" and not spec.required_language_tier:
            continue  # graph value is asserted on risky targets and explicit language-tier floors
        # Accumulate ALL failures; a clone/setup-graph/assess infra error on one task is recorded as a
        # PreflightError line, not raised mid-loop as a raw CLIError that hides the other tasks.
        try:
            dest = out_dir / "graph_preflight" / spec.task_id / "repo"
            repo_path = _clone_fresh(external, dest, out_dir=out_dir)
            if setup_graph_fn is not None:
                setup_graph_fn(repo_path)
            payload = assess_fn(repo_path, spec) if spec.required_language_tier else None
            repo_capability = _capability_for_spec(repo_path, spec)
            _record_capability(repo_capability)
            # Independent validity: legacy C# tasks keep the C# node floor; tiered non-C# tasks use the
            # measured language node_count from the assess payload, so multi-language fixtures are not
            # blocked by a C#-specific validity guard.
            counts = node_count_fn(repo_path)
            _record_coverage(payload)
            node_msg = _node_count_failure(spec, counts, payload, repo_capability)
            if node_msg:
                failures.append(node_msg)
                continue
            if spec.harm_label != "risky" and spec.required_language_tier:
                continue
            if payload is None:
                payload = assess_fn(repo_path, spec)
                _record_coverage(payload)
            msg = _graph_backed_failure(spec, payload)
            if msg:
                failures.append(msg)
        except PreflightError as exc:
            failures.append(f"{spec.task_id}: {exc.args[0] if exc.args else exc}")
        except Exception as exc:  # noqa: BLE001 - infra error (clone/setup-graph/assess), recorded
            failures.append(f"{spec.task_id}: infrastructure error: {type(exc).__name__}: {exc}")
    # Additive observability artifact (never the resume file): the run observatory renders this as the
    # per-language coverage panel. Written before the failure raise so a failed preflight still records
    # whatever coverage it measured.
    try:
        run_artifacts.atomic_write_json(out_dir / "preflight" / "coverage.json",
                                        {"by_language": coverage_by_language})
    except OSError:
        pass
    if failures:
        raise PreflightError("graph pre-flight failed (treatment intervention not proven):\n"
                             + "\n".join(failures))


# ---- revise-safer route calibration -----------------------------------------------------------


def _expected_loss(payload: dict[str, Any]) -> float | None:
    value = (payload.get("scores") or {}).get("expected_loss")
    return float(value) if isinstance(value, (int, float)) else None


def _benefit_discrimination_failure(
    spec: TaskSpec, bad: dict[str, Any], fixed: dict[str, Any]
) -> str | None:
    """Require a real, finite candidate-specific benefit signal when the corpus promises one."""
    if not spec.requires_measured_benefit:
        return None
    gains: list[float] = []
    benefits: list[float] = []
    for label, payload in (("bad", bad), ("reference", fixed)):
        scores = payload.get("scores") or {}
        breakdown = scores.get("benefit_breakdown") or {}
        gain = breakdown.get("maintainability_gain")
        benefit = scores.get("benefit")
        if breakdown.get("source_type") != "measured" or not isinstance(gain, (int, float)):
            return f"{spec.task_id}: {label} route did not expose measured benefit"
        if (
            isinstance(gain, bool)
            or not math.isfinite(gain)
            or isinstance(benefit, bool)
            or not isinstance(benefit, (int, float))
            or not math.isfinite(benefit)
        ):
            return f"{spec.task_id}: {label} route exposed non-finite measured benefit"
        gains.append(float(gain))
        benefits.append(float(benefit))
    if math.isclose(benefits[0], benefits[1], rel_tol=0.0, abs_tol=1e-12):
        return (
            f"{spec.task_id}: decision-driving benefit did not vary between bad and reference "
            f"candidates ({benefits[0]} == {benefits[1]}; measured gains {gains})"
        )
    if benefits[1] <= 0.0:
        return f"{spec.task_id}: reference candidate has no positive benefit ({benefits[1]})"
    return None


def _live_revise_safer_assess(
    repo_path: Path,
    spec: TaskSpec,
    proposed_patch: str,
    db: Path,
    *,
    revise_safer_attempt: int = 0,
    max_revise_safer_attempts: int = 1,
    trusted_candidate_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Live revise-route calibration assess via the same CLI boundary as the treatment advisory."""
    from e2e.experiments.agent_ab.tools import advisory_check_real  # noqa: PLC0415
    from e2e.utils import cli_harness  # noqa: PLC0415

    target = spec.expected_edit_scope[0] if spec.expected_edit_scope else ""
    request = advisory_check_real._build_request({  # noqa: SLF001 - shared e2e request builder
        "target_file": target,
        "change_summary": spec.description,
        "proposed_patch": proposed_patch,
    }, revise_safer_attempt=revise_safer_attempt,
       max_revise_safer_attempts=max_revise_safer_attempts)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(request, fh)
        req_path = fh.name
    verification_path: str | None = None
    if trusted_candidate_verification is not None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(trusted_candidate_verification, fh)
            verification_path = fh.name
    try:
        return cli_harness.assess(
            req_path,
            repo_root=repo_path,
            db=db,
            trusted_candidate_verification_path=verification_path,
            extra_env={"PEBRA_CODEGRAPH_SEMANTIC_DIFF": "1"},
        )
    finally:
        Path(req_path).unlink(missing_ok=True)
        if verification_path is not None:
            Path(verification_path).unlink(missing_ok=True)


def _live_candidate_verification(repo_path: Path, spec: TaskSpec, patch_text: str) -> dict[str, Any]:
    scratch = candidate_materializer.materialize_candidate(repo_path, patch_text)
    if scratch is None:
        return {
            "status": "unavailable",
            "required_checks": ["candidate_build"],
            "domain": "candidate_build",
            "verified_patch_hash": candidate_verifier.candidate_patch_hash(patch_text),
            "reason": "candidate patch did not apply cleanly",
        }
    try:
        return candidate_verifier.verify_candidate(
            repo_path=scratch,
            patch_text=patch_text,
            language=spec.language,
            build_solution=spec.build_solution,
            harness_id=spec.harness_id,
            build_profile=spec.build_profile,
            build_selector=spec.build_selector,
            allow_build_fallback=spec.language in {"javascript", "typescript"},
        )
    finally:
        candidate_materializer.cleanup(scratch)


def run_revise_safer_calibration(
    corpus: list[TaskSpec],
    external: rs.ExternalRepo,
    *,
    out_dir: Path,
    assess_fn: Callable[..., dict[str, Any]] | None = None,
    candidate_verification_fn: Callable[[Path, TaskSpec, str], dict[str, Any]] | None = None,
    setup_graph_fn: Callable[[Path], None] | None = None,
    patch_dir: Path | None = None,
    correct_patch_dir: Path | None = None,
) -> None:
    """Assert the revise-safer route before spending live agent calls.

    For each risky task with a reference correct-fix patch, the intentional bad route must produce
    ``revise_safer``. Legacy tasks then require the reference route to be non-blocking and lower
    expected loss. JS/TS graph-repair tasks instead require the reference route to pass host-produced
    candidate verification and proceed at attempt 1 with cap 2, which is the live repair-arm mechanism.
    """
    assess_fn = assess_fn or _live_revise_safer_assess
    candidate_verification_fn = candidate_verification_fn or _live_candidate_verification
    failures: list[str] = []
    risky_seen = 0
    checked = 0
    for spec in corpus:
        if spec.harm_label != "risky":
            continue
        risky_seen += 1
        spec_patch_dir = patch_dir or _oracle_patch_dir(spec)
        spec_correct_patch_dir = correct_patch_dir or _correct_patch_dir(spec)
        patch_file = spec_patch_dir / f"{spec.task_id}.patch"
        correct_patch = spec_correct_patch_dir / f"{spec.task_id}.patch"
        missing = [str(p) for p in (patch_file, correct_patch) if not p.exists()]
        if missing:
            failures.append(f"{spec.task_id}: missing revise-safer calibration patch: {', '.join(missing)}")
            continue
        checked += 1
        try:
            dest = out_dir / "revise_calibration" / spec.task_id / "repo"
            repo_path = _clone_fresh(external, dest, out_dir=out_dir)
            if setup_graph_fn is not None:
                setup_graph_fn(repo_path)
            bad_db = dest.parent / "bad_revise_calibration.db"
            reference_db = dest.parent / "reference_revise_calibration.db"
            bad_db.unlink(missing_ok=True)
            reference_db.unlink(missing_ok=True)
            bad = assess_fn(
                repo_path,
                spec,
                patch_file.read_text(encoding="utf-8"),
                bad_db,
                revise_safer_attempt=0,
            )
            bad_decision = bad.get("recommended_decision")
            if bad_decision != "revise_safer":
                failures.append(
                    f"{spec.task_id}: expected bad route to return revise_safer, got {bad_decision!r}"
                )
                continue
            correct_patch_text = correct_patch.read_text(encoding="utf-8")
            if spec.language in {"javascript", "typescript"}:
                verification = candidate_verification_fn(repo_path, spec, correct_patch_text)
                if verification.get("status") != "passed":
                    failures.append(
                        f"{spec.task_id}: reference route candidate verification did not pass "
                        f"({verification.get('status')!r}: {verification.get('reason', '')})"
                    )
                    continue
                fixed = assess_fn(
                    repo_path,
                    spec,
                    correct_patch_text,
                    reference_db,
                    revise_safer_attempt=1,
                    max_revise_safer_attempts=2,
                    trusted_candidate_verification=verification,
                )
            else:
                fixed = assess_fn(
                    repo_path,
                    spec,
                    correct_patch_text,
                    reference_db,
                    revise_safer_attempt=0,
                )
            fixed_decision = fixed.get("recommended_decision")
            benefit_failure = _benefit_discrimination_failure(spec, bad, fixed)
            if benefit_failure:
                failures.append(benefit_failure)
                continue
            if fixed_decision in _BLOCKING_DECISIONS:
                failures.append(
                    f"{spec.task_id}: reference route remained blocked ({fixed_decision!r})"
                )
                continue
            if spec.language in {"javascript", "typescript"}:
                gates = fixed.get("gates_fired") or []
                if not any(g.get("name") == "candidate_verification_passed" for g in gates):
                    failures.append(
                        f"{spec.task_id}: verified reference route did not prove candidate "
                        "verification gate 7"
                    )
                continue
            bad_loss = _expected_loss(bad)
            fixed_loss = _expected_loss(fixed)
            if bad_loss is None or fixed_loss is None:
                failures.append(f"{spec.task_id}: calibration missing expected_loss")
            elif fixed_loss >= bad_loss:
                failures.append(
                    f"{spec.task_id}: reference route did not lower expected_loss "
                    f"({fixed_loss} >= {bad_loss})"
                )
        except PreflightError as exc:
            failures.append(f"{spec.task_id}: {exc.args[0] if exc.args else exc}")
        except Exception as exc:  # noqa: BLE001 - infra error recorded with the task id
            failures.append(f"{spec.task_id}: infrastructure error: {type(exc).__name__}: {exc}")
    if risky_seen and checked == 0:
        failures.append("revise-safer calibration validated zero risky patch pairs")
    if failures:
        raise PreflightError("revise-safer calibration failed:\n" + "\n".join(failures))
