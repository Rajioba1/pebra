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
        kwargs = {
            "profile": getattr(spec, "build_profile", "default"),
            "selector": getattr(spec, "build_selector", None),
        }
        if hasattr(spec, "command_timeout"):
            kwargs.update(timeout=spec.command_timeout, install_timeout=min(spec.command_timeout, 120))
        return self._nh.run_build(repo_root, **kwargs)

    def run_build_delta(self, repo_root: Any, spec: Any, *, baseline_keys: Any = None) -> Any:
        return self.run_build(repo_root, spec)  # no compiler-diagnostic delta for the node build

    def run_tests(
        self, repo_root: Any, spec: Any, *, project: Any = None, test_filter: str | None = None
    ) -> Any:
        if project is None:
            project = getattr(spec, "test_selector", None)
        kwargs = {"test_path": project, "test_filter": test_filter}
        if hasattr(spec, "command_timeout"):
            kwargs.update(timeout=spec.command_timeout, install_timeout=min(spec.command_timeout, 120))
        return self._nh.run_tests(repo_root, **kwargs)
