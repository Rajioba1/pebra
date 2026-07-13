"""Shared specimen corpus loader: join tasks.jsonl (agent-facing) to oracles.jsonl (hidden labels).

Validation is the corpus's honesty guard:
  - the agent-facing text must contain none of the experiment/framing words (would leak the setup);
  - every task must have a matching oracle, a valid harm_label, and a non-empty expected_edit_scope;
  - risky tasks must declare a real harm mechanism; safe tasks must not be expected to break the build.
"""

from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath

from e2e.experiments.agent_ab.forbidden import CORPUS_FORBIDDEN_TERMS, match_terms
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.path_scope import is_in_scope

_VALID_HARM = {"risky", "safe"}
_REAL_HARM_TYPES = {"build_failure", "test_failure", "scope_drift"}
_VALID_LANGUAGE_TIERS = {"risk_only", "partial", "full"}
# The build/test backend this task's specimen uses (see backends/). csharp is the default (the original
# Math.NET/Avalonia specimen); javascript/typescript share the node backend.
_VALID_LANGUAGES = {"csharp", "javascript", "typescript"}
_DEFAULT_HARNESS_BY_LANGUAGE = {"csharp": "dotnet", "javascript": "node", "typescript": "node"}
_VALID_HARNESSES = {"dotnet", "node"}
_DEFAULT_REPO_IDENTITY_BY_SPECIMEN = {"javascript": ("package.json", "pnpm-lock.yaml")}
# Fixed build/test profiles (never a shell command). "zshy" is the node type-check profile (tsc-based
# build tool, e.g. Zod) run as `pnpm --filter <pkg> exec zshy --project <tsconfig>` from build_selector.
_VALID_BUILD_PROFILES = {"default", "zshy"}
_VALID_TEST_PROFILES = {"default"}


class CorpusError(ValueError):
    pass


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{path.name}:{i} is not valid JSON: {exc}") from exc
    return rows


def _agent_facing_text(task: dict) -> str:
    return " ".join([task.get("task_id", ""), task.get("description", ""),
                     *task.get("target_hints", [])])


def _leak_terms(text: str) -> list[str]:
    return list(match_terms(text, CORPUS_FORBIDDEN_TERMS))


def _valid_zshy_selector(selector: str | None) -> bool:
    if not selector:
        return False
    pkg, sep, tsconfig = selector.partition(":")
    return bool(sep and pkg.strip() and tsconfig.strip())


def _string_list(oracle: dict, field: str, task_id: str) -> tuple[str, ...]:
    value = oracle.get(field, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CorpusError(f"task {task_id!r} has invalid {field}; expected non-empty strings")
    return tuple(value)


def _repo_relative_path(value: object, field: str, task_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"task {task_id!r} has invalid {field}")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or any(":" in part for part in posix.parts)
    ):
        raise CorpusError(f"task {task_id!r} has invalid {field}; expected repo-relative path")
    return normalized


def load_corpus(
    tasks_path: Path,
    oracles_path: Path,
    *,
    specimen: str = "csharp",
    default_repo_identity_files: tuple[str, ...] | None = None,
) -> list[TaskSpec]:
    tasks = _read_jsonl(tasks_path)
    oracle_rows = _read_jsonl(oracles_path)
    oracles: dict[str, dict] = {}
    for oracle in oracle_rows:
        tid = oracle.get("task_id")
        if not tid:
            raise CorpusError("an oracle row is missing task_id")
        if tid in oracles:
            raise CorpusError(f"duplicate oracle task_id {tid!r}")
        oracles[tid] = oracle

    specs: list[TaskSpec] = []
    seen: set[str] = set()
    for task in tasks:
        tid = task.get("task_id")
        if not tid:
            raise CorpusError("a task row is missing task_id")
        if tid in seen:
            raise CorpusError(f"duplicate task_id {tid!r}")
        seen.add(tid)

        leaks = _leak_terms(_agent_facing_text(task))
        if leaks:
            raise CorpusError(f"task {tid!r} agent-facing text leaks experiment terms: {leaks}")

        oracle = oracles.get(tid)
        if oracle is None:
            raise CorpusError(f"task {tid!r} has no oracle label in oracles.jsonl")

        harm_label = oracle.get("harm_label")
        if harm_label not in _VALID_HARM:
            raise CorpusError(f"task {tid!r} has invalid harm_label {harm_label!r}")
        scope = tuple(oracle.get("expected_edit_scope", ()))
        if not scope:
            raise CorpusError(f"task {tid!r} has empty expected_edit_scope")
        harm_type = oracle.get("harm_type", "none")
        must_fail = bool(oracle.get("oracle_build_must_fail", False))
        evaluator_test_project = _repo_relative_path(
            oracle.get("evaluator_test_project"), "evaluator_test_project", tid
        )
        evaluator_test_filter = oracle.get("evaluator_test_filter")
        completion_test_project = _repo_relative_path(
            oracle.get("completion_test_project"), "completion_test_project", tid
        )
        completion_test_filter = oracle.get("completion_test_filter")
        required_task_files = _string_list(oracle, "required_task_files", tid)
        required_task_symbols = _string_list(oracle, "required_task_symbols", tid)
        required_task_checks = _string_list(oracle, "required_task_checks", tid)
        behavior_oracle = bool(oracle.get("behavior_oracle", False))
        build_solution = oracle.get("build_solution", "TemplateBlueprint.sln")
        if "repo_identity_files" in oracle:
            repo_identity_files = tuple(oracle["repo_identity_files"])
        elif default_repo_identity_files is not None:
            repo_identity_files = tuple(default_repo_identity_files)
        elif specimen == "csharp" and build_solution:
            repo_identity_files = (build_solution,)
        else:
            repo_identity_files = _DEFAULT_REPO_IDENTITY_BY_SPECIMEN.get(specimen, ())
        required_language_tier = oracle.get("required_language_tier")
        language = oracle.get("language", "csharp")
        if language not in _VALID_LANGUAGES:
            raise CorpusError(f"task {tid!r} has invalid language {language!r}")
        harness_id = oracle.get("harness_id") or _DEFAULT_HARNESS_BY_LANGUAGE[language]
        if harness_id not in _VALID_HARNESSES:
            raise CorpusError(f"task {tid!r} has invalid harness_id {harness_id!r}")
        expected_harness = _DEFAULT_HARNESS_BY_LANGUAGE[language]
        if harness_id != expected_harness:
            raise CorpusError(
                f"task {tid!r} language {language!r} requires harness_id {expected_harness!r}"
            )
        build_profile = oracle.get("build_profile", "default")
        test_profile = oracle.get("test_profile", "default")
        test_selector = oracle.get("test_selector")
        build_selector = oracle.get("build_selector")
        if build_profile not in _VALID_BUILD_PROFILES:
            raise CorpusError(f"task {tid!r} has invalid build_profile {build_profile!r}")
        if test_profile not in _VALID_TEST_PROFILES:
            raise CorpusError(f"task {tid!r} has invalid test_profile {test_profile!r}")
        if build_profile == "zshy" and harness_id != "node":
            raise CorpusError(f"task {tid!r} build_profile 'zshy' requires the node harness")
        if build_profile == "zshy" and not _valid_zshy_selector(build_selector):
            raise CorpusError(
                f"task {tid!r} build_profile 'zshy' requires build_selector 'pkg:tsconfig'"
            )

        if harm_label == "risky" and not (must_fail or harm_type in _REAL_HARM_TYPES):
            raise CorpusError(f"risky task {tid!r} declares no real harm mechanism")
        if harm_type == "test_failure" and not evaluator_test_project:
            raise CorpusError(f"test_failure task {tid!r} must declare evaluator_test_project")
        if behavior_oracle and not evaluator_test_project:
            raise CorpusError(f"task {tid!r} behavior_oracle requires evaluator_test_project")
        if completion_test_project and (
            not behavior_oracle or completion_test_project == evaluator_test_project
        ):
            raise CorpusError(
                f"task {tid!r} completion_test_project requires a distinct behavior oracle"
            )
        for required_file in required_task_files:
            normalized_required = _repo_relative_path(
                required_file, "required_task_files", tid
            )
            if normalized_required is None or not is_in_scope(normalized_required, scope):
                raise CorpusError(
                    f"task {tid!r} required_task_files must stay within expected_edit_scope"
                )
        if harm_label == "safe" and must_fail:
            raise CorpusError(f"safe task {tid!r} must not be expected to break the build")
        if required_language_tier is not None and required_language_tier not in _VALID_LANGUAGE_TIERS:
            raise CorpusError(
                f"task {tid!r} has invalid required_language_tier {required_language_tier!r}"
            )
        p_success = oracle.get("assay_p_success", 0.75)
        immediate_benefit = oracle.get("assay_immediate_benefit", 0.5)
        review_cost = oracle.get("assay_review_cost", 0.1)
        if (
            isinstance(p_success, bool)
            or not isinstance(p_success, (int, float))
            or not math.isfinite(p_success)
            or not 0.0 <= p_success <= 1.0
        ):
            raise CorpusError(f"task {tid!r} has invalid assay_p_success {p_success!r}")
        for name, value in (
            ("assay_immediate_benefit", immediate_benefit),
            ("assay_review_cost", review_cost),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise CorpusError(f"task {tid!r} has invalid {name} {value!r}")

        specs.append(TaskSpec(
            task_id=tid,
            description=task.get("description", ""),
            target_hints=tuple(task.get("target_hints", ())),
            harm_label=harm_label,
            expected_edit_scope=scope,
            harm_type=harm_type,
            oracle_build_must_fail=must_fail,
            evaluator_test_project=evaluator_test_project,
            evaluator_test_filter=evaluator_test_filter,
            completion_test_project=completion_test_project,
            completion_test_filter=completion_test_filter,
            required_task_files=required_task_files,
            required_task_symbols=required_task_symbols,
            required_task_checks=required_task_checks,
            build_solution=build_solution,
            required_language_tier=required_language_tier,
            requires_measured_benefit=bool(oracle.get("requires_measured_benefit", False)),
            requires_natural_safe_route=bool(oracle.get("requires_natural_safe_route", False)),
            requires_graph_refinement_route=bool(
                oracle.get("requires_graph_refinement_route", False)
            ),
            assay_p_success=float(p_success),
            assay_immediate_benefit=float(immediate_benefit),
            assay_review_cost=float(review_cost),
            language=language,
            harness_id=harness_id,
            specimen=specimen,
            repo_identity_files=repo_identity_files,
            build_profile=build_profile,
            test_profile=test_profile,
            test_selector=test_selector,
            build_selector=build_selector,
            behavior_oracle=behavior_oracle,
        ))
    return specs
