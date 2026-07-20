"""Bounded provider adapter for explicit repository exploration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pebra.adapters.codegraph_adapter import CodeGraphAdapter
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine
from pebra.core.exploration import (
    ExplorationResult,
    bounded_context,
    clamp_bounds,
    unavailable_result,
)
from pebra.core.graph_snapshot import GraphSnapshot


_AFFECTED_KEYS = {"changedFiles", "affectedTests", "totalDependentsTraversed"}


class CodeGraphExplorer:
    def __init__(
        self,
        *,
        graph_adapter: CodeGraphAdapter | None = None,
        runner: Callable[..., Any] = subprocess.run,
        engine_fn: Callable[[], str | None] | None = None,
    ) -> None:
        self._graph = graph_adapter or CodeGraphAdapter()
        self._runner = runner
        self._engine_fn = engine_fn or find_engine

    def prepare(self, repo_root: str) -> GraphSnapshot:
        return self._graph.prepare(repo_root)

    @staticmethod
    def _files(repo_root: str, files: tuple[str, ...]) -> tuple[str, ...]:
        root = Path(repo_root).resolve()
        normalized: list[str] = []
        seen: set[str] = set()
        for value in files:
            candidate = Path(value)
            try:
                relative = (
                    candidate.resolve().relative_to(root)
                    if candidate.is_absolute()
                    else candidate
                )
            except (OSError, ValueError):
                continue
            path = relative.as_posix()
            while path.startswith("./"):
                path = path[2:]
            if path and path != "." and path not in seen:
                seen.add(path)
                normalized.append(path)
        return tuple(normalized)

    def _run(self, engine: str, args: list[str], timeout: int) -> tuple[str | None, str | None]:
        try:
            proc = self._runner(
                resolve_engine_argv(engine, args),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)
        if proc.returncode != 0:
            return None, f"codegraph query exited {proc.returncode}"
        return str(proc.stdout), None

    @staticmethod
    def _affected_tests(raw: str) -> tuple[tuple[str, ...], str | None]:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return (), "codegraph affected output was malformed"
        if not isinstance(payload, dict) or set(payload) != _AFFECTED_KEYS:
            return (), "codegraph affected output was malformed"
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
            return (), "codegraph affected output was malformed"
        return tuple(sorted({value.replace("\\", "/") for value in tests if value})), None

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
        affected_tests: tuple[str, ...] = ()
        dependent_files: set[str] = set()

        if query:
            context_raw, error = self._run(
                engine,
                ["explore", query, "--path", repo_root, "--max-files", str(max_files)],
                60,
            )
            if error is not None:
                failed = replace(snapshot, status="error", fallback_reason=error)
                return unavailable_result(failed, error)
            context = context_raw or ""

        if normalized_files:
            affected_raw, error = self._run(
                engine,
                ["affected", *normalized_files, "--path", repo_root, "--json"],
                60,
            )
            if error is not None:
                failed = replace(snapshot, status="error", fallback_reason=error)
                return unavailable_result(failed, error)
            affected_tests, warning = self._affected_tests(affected_raw or "")
            if warning is not None:
                warnings.append(warning)
            for target in normalized_files:
                dependency = self._graph.dependent_files_result(target, repo_root)
                if not dependency.get("available"):
                    warning = dependency.get("fallback_reason") or (
                        f"dependent files unavailable for {target}"
                    )
                    if warning not in warnings:
                        warnings.append(str(warning))
                    continue
                dependent_files.update(
                    str(value).replace("\\", "/")
                    for value in dependency.get("dependent_files", ())
                    if value
                )

        if not self._graph.revalidate_snapshot(repo_root, snapshot):
            failed = replace(
                snapshot,
                status="stale",
                fallback_reason="repository or graph changed during exploration",
            )
            return unavailable_result(failed, failed.fallback_reason or "snapshot changed")
        context, truncated = bounded_context(context, max_bytes)
        return ExplorationResult(
            status="available",
            snapshot=snapshot,
            context=context,
            dependent_files=tuple(sorted(dependent_files)),
            affected_tests=affected_tests,
            warnings=tuple(warnings),
            fallback_reason=None,
            truncated=truncated,
        )
