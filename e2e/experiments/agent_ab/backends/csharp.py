"""CSharpBackend — the original dotnet toolchain, behind the BuildBackend seam. Behaviour-identical to
the pre-seam direct ``dotnet_harness`` calls (same sln, same args)."""

from __future__ import annotations

from typing import Any

from e2e.experiments.agent_ab.backends.base import spec_solution
from e2e.external.utils import dotnet_harness as _dn


class CSharpBackend:
    language = "csharp"

    def __init__(self, harness: Any = _dn) -> None:
        self._dn = harness

    def run_build(self, repo_root: Any, spec: Any) -> Any:
        return self._dn.run_build(repo_root, sln=spec_solution(spec))

    def run_build_delta(self, repo_root: Any, spec: Any, *, baseline_keys: Any = None) -> Any:
        return self._dn.run_build_delta(repo_root, sln=spec_solution(spec), baseline_keys=baseline_keys)

    def run_tests(
        self, repo_root: Any, spec: Any, *, project: Any = None, test_filter: str | None = None
    ) -> Any:
        return self._dn.run_tests(
            repo_root, sln=spec_solution(spec), project=project, test_filter=test_filter
        )
