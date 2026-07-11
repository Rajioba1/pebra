"""Post-agent HIDDEN oracle: inject a neutral evaluator test project into the AGENT'S RESULT tree,
then build + test.

The agent never sees these tests during its run (they are copied in only after it stops and its diff
is captured), so it cannot read them (teach-to-test), delete them, or infer the trap. Injecting them
post-hoc and running ``dotnet build`` + ``dotnet test`` answers the strong endpoint: did the agent ship
code that still passes the real project checks?

A C# task with no ``specimens/csharp/corpus/evaluator_tests/<task_id>/`` directory gets build-only
scoring (build-break efficacy); one with an evaluator project gets build+test scoring
(build+test+scope efficacy). Build/test are injectable for unit tests. No pebra import.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab import backends
from e2e.experiments.agent_ab.models import TaskSpec

_EVALUATOR_DIR = (
    Path(__file__).resolve().parents[1] / "specimens" / "csharp" / "corpus" / "evaluator_tests"
)


def _task_evaluator_dir(task: TaskSpec, evaluator_dir: Path | None = None) -> Path:
    if evaluator_dir is not None:
        return evaluator_dir
    return (
        Path(__file__).resolve().parents[1]
        / "specimens" / task.specimen / "corpus" / "evaluator_tests"
    )


def inject_task_evaluator(
    repo_path: Path, task: TaskSpec, *, evaluator_dir: Path | None = None
) -> Path | None:
    """Copy a hidden specimen test into its declared repo-relative destination post-agent."""
    if not task.evaluator_test_project:
        return None
    src_root = _task_evaluator_dir(task, evaluator_dir) / task.task_id
    source_project = src_root / task.evaluator_test_project
    if not source_project.is_file():
        return None
    shutil.copytree(src_root, repo_path, dirs_exist_ok=True)
    project = repo_path / task.evaluator_test_project
    return project if project.is_file() else None


def inject_evaluator_tests(
    repo_path: Path, task_id: str, *, evaluator_dir: Path | None = None
) -> Path | None:
    """Copy the task's evaluator test project into the clone (post-agent) and return the injected
    ``.csproj`` path within the clone, or None.

    Returns None when there is no ``<task_id>/`` directory OR the directory contains no ``.csproj``
    (a test dir with no project = no test signal, scored honestly as no-test). Returning the concrete
    project path lets the caller run ``dotnet test <project>`` directly rather than against the
    solution — the solution may not reference the injected project, and ``dotnet test <solution>``
    exits 0 ("no tests ran") in that case, which would fabricate a pass."""
    src = (evaluator_dir or _EVALUATOR_DIR) / task_id
    if not src.is_dir():
        return None
    csprojs = sorted(src.rglob("*.csproj"))
    if not csprojs:
        return None
    shutil.copytree(src, repo_path, dirs_exist_ok=True)
    return repo_path / csprojs[0].relative_to(src)


def _task_id(task: str | TaskSpec) -> str:
    return task.task_id if isinstance(task, TaskSpec) else task


def _existing_test_project(repo_path: Path, task: str | TaskSpec) -> tuple[Path | None, str | None]:
    if not isinstance(task, TaskSpec) or not task.evaluator_test_project:
        return None, None
    project = (repo_path / task.evaluator_test_project).resolve()
    return (project if project.is_file() else None), task.evaluator_test_filter


def _run_build(repo_path: Path, task: str | TaskSpec, build_fn: Callable[[Path], Any] | None):
    if build_fn is not None:
        return build_fn(repo_path)
    backend = backends.backend_for_spec(task) if isinstance(task, TaskSpec) else backends.get_backend("csharp")
    return backend.run_build(repo_path, task)


def run_evaluator(
    repo_path: Path,
    task: str | TaskSpec,
    *,
    evaluator_dir: Path | None = None,
    build_fn: Callable[[Path], Any] | None = None,
    test_fn: Callable[..., Any] | None = None,
) -> tuple[Any, Any | None, bool]:
    """Inject the hidden test project (if any), then build; if a project was injected and the build
    passed, run ``dotnet test`` against THAT project (not the solution).

    Returns (build_result, test_result_or_None, injected)."""
    backend = backends.backend_for_spec(task) if isinstance(task, TaskSpec) else backends.get_backend("csharp")
    project, test_filter = _existing_test_project(repo_path, task)
    injected = False
    if project is None and isinstance(task, TaskSpec):
        project = inject_task_evaluator(repo_path, task, evaluator_dir=evaluator_dir)
        injected = project is not None
        if project is None and task.language == "csharp":
            project = inject_evaluator_tests(repo_path, task.task_id, evaluator_dir=evaluator_dir)
            injected = project is not None
    elif project is None:
        project = inject_evaluator_tests(repo_path, _task_id(task), evaluator_dir=evaluator_dir)
        injected = project is not None
    build = _run_build(repo_path, task, build_fn)
    if project is not None and build.ran and build.passed:
        if test_fn is not None:
            test = (test_fn(repo_path, project=project, test_filter=test_filter)
                    if test_filter else test_fn(repo_path, project=project))
        else:
            test = backend.run_tests(repo_path, task, project=project, test_filter=test_filter)
    else:
        test = None
    return build, test, injected
