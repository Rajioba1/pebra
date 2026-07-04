"""Post-agent HIDDEN oracle: inject a neutral evaluator test project into the AGENT'S RESULT tree,
then build + test.

The agent never sees these tests during its run (they are copied in only after it stops and its diff
is captured), so it cannot read them (teach-to-test), delete them, or infer the trap. Injecting them
post-hoc and running ``dotnet build`` + ``dotnet test`` answers the strong endpoint: did the agent ship
code that still passes the real project checks?

A task with no ``corpus/evaluator_tests/<task_id>/`` directory gets build-only scoring (build-break
efficacy); one with an evaluator project gets build+test scoring (build+test+scope efficacy). Build/test
are injectable for unit tests. No pebra import.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.external.utils import dotnet_harness as dn

_EVALUATOR_DIR = Path(__file__).resolve().parents[1] / "corpus" / "evaluator_tests"


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
    return (repo_path / task.evaluator_test_project).resolve(), task.evaluator_test_filter


def _run_build(repo_path: Path, task: str | TaskSpec, build_fn: Callable[[Path], Any] | None):
    if build_fn is not None:
        return build_fn(repo_path)
    sln = task.build_solution if isinstance(task, TaskSpec) else "TemplateBlueprint.sln"
    return dn.run_build(repo_path, sln=sln)


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
    test_fn = test_fn or dn.run_tests
    project, test_filter = _existing_test_project(repo_path, task)
    injected = False
    if project is None:
        project = inject_evaluator_tests(repo_path, _task_id(task), evaluator_dir=evaluator_dir)
        injected = project is not None
    build = _run_build(repo_path, task, build_fn)
    if project is not None and build.ran and build.passed:
        test = (test_fn(repo_path, project=project, test_filter=test_filter)
                if test_filter else test_fn(repo_path, project=project))
    else:
        test = None
    return build, test, injected
