"""Bounded provider adapter for explicit repository exploration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pebra.adapters.bounded_process import run_bounded
from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.core.engine_argv import UnsafeEngineLauncherError, resolve_engine_argv
from pebra.core.engine_paths import find_engine
from pebra.core.exploration import (
    ExplorationResult,
    bounded_context,
    clamp_bounds,
    normalize_repository_files,
    unavailable_result,
)
from pebra.core.graph_snapshot import GraphSnapshot


_AFFECTED_KEYS = {"changedFiles", "affectedTests", "totalDependentsTraversed"}
_AFFECTED_BYTES = 256_000
_DIAGNOSTIC_BYTES = 16_384
_PATH_BYTES = 4_096


class CodeGraphExplorer:
    def __init__(
        self,
        *,
        graph_adapter: CodeGraphAdapter | None = None,
        runner: Callable[..., Any] | None = None,
        engine_fn: Callable[[], str | None] | None = None,
    ) -> None:
        self._graph = graph_adapter or CodeGraphAdapter()
        self._runner = runner
        self._engine_fn = engine_fn or find_engine

    def prepare(self, repo_root: str) -> GraphSnapshot:
        return self._graph.prepare(repo_root)

    @staticmethod
    def _files(repo_root: str, files: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_repository_files(repo_root, files)

    @staticmethod
    def _cap_text(value: str, limit: int) -> tuple[str, bool]:
        raw = value.encode("utf-8")
        if len(raw) <= limit:
            return value, False
        return raw[:limit].decode("utf-8", errors="ignore"), True

    def _run(
        self, engine: str, args: list[str], timeout: int, stdout_limit: int
    ) -> tuple[str | None, str | None, bool]:
        try:
            argv = resolve_engine_argv(engine, args)
        except UnsafeEngineLauncherError as exc:
            return None, str(exc), False
        except OSError:
            return None, "codegraph query launch failed", False
        if self._runner is None:
            bounded = run_bounded(
                argv,
                timeout=timeout,
                stdout_limit=stdout_limit,
                stderr_limit=_DIAGNOSTIC_BYTES,
            )
            if bounded.error == "timeout":
                return None, "codegraph query timed out", bounded.stdout_truncated
            if bounded.error == "launch_failed":
                return None, "codegraph query launch failed", bounded.stdout_truncated
            if bounded.returncode != 0:
                return None, "codegraph query failed", bounded.stdout_truncated
            return bounded.stdout, None, bounded.stdout_truncated or bounded.stderr_truncated
        try:
            proc = self._runner(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "codegraph query timed out", False
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return None, "codegraph query launch failed", False
        if proc.returncode != 0:
            return None, "codegraph query failed", False
        stdout, stdout_truncated = self._cap_text(str(proc.stdout), stdout_limit)
        _, stderr_truncated = self._cap_text(str(getattr(proc, "stderr", "")), _DIAGNOSTIC_BYTES)
        return stdout, None, stdout_truncated or stderr_truncated

    @staticmethod
    def _bounded_paths(values: Any, limit: int) -> tuple[tuple[str, ...], bool]:
        if not isinstance(values, (list, tuple, set)):
            return (), True
        normalized: set[str] = set()
        truncated = False
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            path = value.replace("\\", "/")
            if len(path.encode("utf-8")) > _PATH_BYTES:
                truncated = True
                continue
            normalized.add(path)
        ordered = sorted(normalized)
        if len(ordered) > limit:
            truncated = True
        return tuple(ordered[:limit]), truncated

    @staticmethod
    def _affected_tests(raw: str, limit: int) -> tuple[tuple[str, ...], str | None, bool]:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, RecursionError, TypeError):
            return (), "codegraph affected output was malformed", False
        if not isinstance(payload, dict) or set(payload) != _AFFECTED_KEYS:
            return (), "codegraph affected output was malformed", False
        changed = payload["changedFiles"]
        tests = payload["affectedTests"]
        traversed = payload["totalDependentsTraversed"]
        if (
            not isinstance(changed, list)
            or not all(isinstance(value, str) for value in changed)
            or not isinstance(tests, list)
            or not all(isinstance(value, str) for value in tests)
            or isinstance(traversed, bool)
            or not isinstance(traversed, int)
            or traversed < 0
        ):
            return (), "codegraph affected output was malformed", False
        bounded, truncated = CodeGraphExplorer._bounded_paths(tests, limit)
        return bounded, None, truncated

    def explore(
        self,
        repo_root: str,
        query: str,
        *,
        snapshot: GraphSnapshot,
        files: tuple[str, ...] = (),
        max_files: int = 8,
        max_bytes: int = 24_000,
    ) -> ExplorationResult:
        if snapshot.status != "available":
            return unavailable_result(
                snapshot, snapshot.fallback_reason or "repository graph unavailable"
            )
        normalized_files = self._files(repo_root, files)
        query = query.strip()
        if not query and not normalized_files:
            failed = replace(
                snapshot, status="error", fallback_reason="query or file is required"
            )
            return unavailable_result(failed, "query or file is required")
        engine = self._engine_fn()
        if engine is None:
            failed = replace(
                snapshot, status="unavailable", fallback_reason="codegraph CLI not found"
            )
            return unavailable_result(failed, "codegraph CLI not found")

        max_files, max_bytes = clamp_bounds(max_files, max_bytes)
        context = ""
        warnings: list[str] = []
        output_truncated = False
        affected_tests: tuple[str, ...] = ()
        dependent_files: set[str] = set()

        if query:
            context_raw, error, provider_truncated = self._run(
                engine,
                ["explore", query, "--path", repo_root, "--max-files", str(max_files)],
                60,
                max_bytes,
            )
            if error is not None:
                failed = replace(snapshot, status="error", fallback_reason=error)
                return unavailable_result(failed, error)
            context = context_raw or ""
            output_truncated = output_truncated or provider_truncated
            if provider_truncated:
                warnings.append("provider context truncated to byte limit")

        if normalized_files:
            affected_raw, error, affected_output_truncated = self._run(
                engine,
                ["affected", *normalized_files, "--path", repo_root, "--json"],
                60,
                _AFFECTED_BYTES,
            )
            if error is not None:
                failed = replace(snapshot, status="error", fallback_reason=error)
                return unavailable_result(failed, error)
            if affected_output_truncated:
                warnings.append("affected output exceeded byte limit")
                output_truncated = True
            else:
                affected_tests, warning, tests_truncated = self._affected_tests(
                    affected_raw or "", max_files
                )
                if warning is not None:
                    warnings.append(warning)
                if tests_truncated:
                    warnings.append("affected tests truncated to result limit")
                    output_truncated = True
            for target in normalized_files:
                dependency = self._graph.dependent_files_result(target, repo_root)
                if not dependency.get("available"):
                    warning = dependency.get("fallback_reason") or (
                        f"dependent files unavailable for {target}"
                    )
                    if warning not in warnings:
                        warnings.append(str(warning))
                    continue
                bounded_dependencies, dependencies_truncated = self._bounded_paths(
                    dependency.get("dependent_files", ()), max_files
                )
                dependent_files.update(bounded_dependencies)
                if dependencies_truncated:
                    output_truncated = True
                    if "dependent files truncated to result limit" not in warnings:
                        warnings.append("dependent files truncated to result limit")

        if not self._graph.revalidate_snapshot(repo_root, snapshot):
            failed = replace(
                snapshot,
                status="stale",
                fallback_reason="repository or graph changed during exploration",
            )
            return unavailable_result(failed, failed.fallback_reason or "snapshot changed")
        context, context_truncated = bounded_context(context, max_bytes)
        if context_truncated and "provider context truncated to byte limit" not in warnings:
            warnings.append("provider context truncated to byte limit")
        bounded_dependents, dependents_truncated = self._bounded_paths(
            dependent_files, max_files
        )
        if dependents_truncated:
            output_truncated = True
            if "dependent files truncated to result limit" not in warnings:
                warnings.append("dependent files truncated to result limit")
        return ExplorationResult(
            status="available",
            snapshot=snapshot,
            context=context,
            dependent_files=bounded_dependents,
            affected_tests=affected_tests,
            warnings=tuple(warnings),
            fallback_reason=None,
            truncated=output_truncated or context_truncated,
        )
