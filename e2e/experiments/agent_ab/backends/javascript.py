"""JavaScriptBackend — the node/pnpm toolchain (JS + TS) behind the BuildBackend seam.

The specimen's ``build_solution`` is irrelevant here; the fixed node profile detects the package manager
and runs the repo's build/test scripts. ``project`` (the evaluator's injected test file/dir) maps to the
Vitest ``test_path``. There is no CS-style diagnostic delta, so ``run_build_delta`` aliases ``run_build``.
"""

from __future__ import annotations

from typing import Any

from e2e.external.utils import node_harness as _nh


class JavaScriptBackend:
    language = "javascript"

    def __init__(self, harness: Any = _nh) -> None:
        self._nh = harness

    def run_build(self, repo_root: Any, spec: Any) -> Any:
        return self._nh.run_build(repo_root)

    def run_build_delta(self, repo_root: Any, spec: Any, *, baseline_keys: Any = None) -> Any:
        return self._nh.run_build(repo_root)  # no compiler-diagnostic delta for the node build

    def run_tests(
        self, repo_root: Any, spec: Any, *, project: Any = None, test_filter: str | None = None
    ) -> Any:
        return self._nh.run_tests(repo_root, test_path=project, test_filter=test_filter)
