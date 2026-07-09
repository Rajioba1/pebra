"""BuildBackend — the language-keyed build/test seam.

Each backend wraps ONE toolchain (dotnet / node) behind a fixed profile. The corpus declares a
``language``; ``get_backend`` resolves the backend; call sites (evaluator, run_pair baseline, agent
tools) go through it instead of hard-wiring ``dotnet_harness``. Results are duck-typed on the fields the
runner reads: ``available``, ``ran``, ``passed``, ``exit_code``, ``error_summary``, ``duration_seconds``
(+ ``tests_selected`` for tests). No pebra import.
"""

from __future__ import annotations

from typing import Any, Protocol

from e2e.experiments.agent_ab.models import TaskSpec


def spec_solution(spec: Any) -> str:
    """The dotnet solution for a spec (TaskSpec | str task_id | None) — default for the C# specimen."""
    if isinstance(spec, TaskSpec) or hasattr(spec, "build_solution"):
        return spec.build_solution
    return "TemplateBlueprint.sln"


class BuildBackend(Protocol):
    language: str

    def run_build(self, repo_root: Any, spec: Any) -> Any: ...

    def run_build_delta(self, repo_root: Any, spec: Any, *, baseline_keys: Any = None) -> Any: ...

    def run_tests(
        self, repo_root: Any, spec: Any, *, project: Any = None, test_filter: str | None = None
    ) -> Any: ...
