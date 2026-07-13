"""Bounded materialized CodeGraph continuity evidence for revised candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path

from pebra.adapters._paths import safe_relative_files
from pebra.adapters.patch_header_adapter import touched_files
from pebra.adapters.patch_materializer import materialize_patch
from pebra.core.graph_version import CODEGRAPH_DEFAULT_VERSION
from pebra.core.engine_paths import find_engine
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.models import (
    CandidateAction,
    CandidateGraphRiskEvidence,
    GraphRiskScope,
    ScopedGraphRiskFact,
)


_SCHEMA_VERSION = 1
_PROVIDER_VERSION = "materialized-continuity-v1"
_CALLABLE_KINDS = {"function", "method", "class", "struct", "interface", "trait", "protocol"}
_ALIAS_BINDING_KINDS = {"constant", "variable"}
_SUPPORTED_EVENTS = {"public_api_break", "api_contract_break"}
_CONFIG_NAMES = (
    "package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
)

Indexer = Callable[[Path], Path]
ContextFiles = Callable[[str, tuple[str, ...]], tuple[str, ...]]


def _elapsed(start: float) -> float:
    return max(0.0, (time.monotonic() - start) * 1000.0)


def _write_files(root: Path, files: Mapping[str, str | None]) -> None:
    for rel, content in files.items():
        if content is None:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))


def _clear(root: Path) -> None:
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _default_context_files(_repo_root: str, _owner_files: tuple[str, ...]) -> tuple[str, ...]:
    return ()


def _index_with_codegraph(root: Path, timeout_s: float = 30.0) -> Path:
    executable = find_engine()
    if executable is None:
        raise FileNotFoundError("codegraph")
    proc = subprocess.run(
        resolve_engine_argv(executable, ["init", str(root)]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise subprocess.SubprocessError("codegraph init failed")
    database = root / ".codegraph" / "codegraph.db"
    if not database.is_file():
        raise FileNotFoundError(str(database))
    return database


def _engine_identity() -> dict[str, object]:
    executable = find_engine()
    if executable is None:
        return {"version": CODEGRAPH_DEFAULT_VERSION, "launcher": None}
    path = Path(executable).resolve()
    try:
        stat = path.stat()
        return {
            "version": CODEGRAPH_DEFAULT_VERSION,
            "launcher": str(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {"version": CODEGRAPH_DEFAULT_VERSION, "launcher": str(path)}


class CodeGraphCandidateRefinementAdapter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        indexer: Indexer | None = None,
        context_files_fn: ContextFiles | None = None,
        max_context_files: int = 40,
        max_context_bytes: int = 5 * 1024 * 1024,
        max_cache_entries: int = 128,
        max_cache_bytes: int = 32 * 1024 * 1024,
        cache_root: Path | None = None,
        max_total_seconds: float = 45.0,
    ) -> None:
        self._enabled = enabled
        self._max_total_seconds = max(1.0, max_total_seconds)
        self._indexer = indexer or (
            lambda root: _index_with_codegraph(
                root, timeout_s=max(1.0, min(20.0, self._max_total_seconds / 2.0))
            )
        )
        self._context_files_fn = context_files_fn or _default_context_files
        self._max_context_files = max(1, max_context_files)
        self._max_context_bytes = max(1, max_context_bytes)
        self._max_cache_entries = max(1, max_cache_entries)
        self._max_cache_bytes = max(1, max_cache_bytes)
        self._cache_root = cache_root or Path.home() / ".pebra" / "cache"

    @staticmethod
    def _config_files(repo_root: str, owner_files: tuple[str, ...]) -> set[str]:
        root = Path(repo_root).resolve()
        found: set[str] = set()
        for owner in owner_files:
            current = (root / owner).parent
            while current == root or root in current.parents:
                for name in _CONFIG_NAMES:
                    candidate = current / name
                    if candidate.is_file():
                        found.add(candidate.relative_to(root).as_posix())
                if current == root:
                    break
                current = current.parent
        return found

    def _context(
        self, action: CandidateAction, repo_root: str, scope: GraphRiskScope
    ) -> tuple[dict[str, str | None], str | None]:
        candidates = set(touched_files(action.proposed_patch or ""))
        candidates.update(scope.owner_file_paths)
        candidates.update(self._config_files(repo_root, scope.owner_file_paths))
        if len(candidates) > self._max_context_files:
            return {}, "context file cap exceeded"
        try:
            expanded = tuple(self._context_files_fn(repo_root, scope.owner_file_paths))
            candidates.update(expanded)
        except Exception:  # noqa: BLE001 - optional context expansion fails closed to touched scope
            expanded = ()
        if scope.expected_consumer_count > 0 and not expanded:
            return {}, "dependent context unavailable"
        paths = tuple(sorted(candidates))
        if len(paths) > self._max_context_files:
            return {}, "context file cap exceeded"
        if safe_relative_files(repo_root, list(paths)) != list(paths):
            return {}, "invalid context file path"
        root = Path(repo_root)
        files: dict[str, str | None] = {}
        total = 0
        try:
            for rel in paths:
                target = root / rel
                content = target.read_bytes().decode("utf-8") if target.is_file() else None
                files[rel] = content
                total += len(content.encode("utf-8")) if content is not None else 0
        except (OSError, UnicodeError):
            return {}, "context file could not be read"
        if total > self._max_context_bytes:
            return files, "context byte cap exceeded"
        return files, None

    def _manifest_hash(
        self,
        action: CandidateAction,
        scope: GraphRiskScope,
        files: Mapping[str, str | None],
        reason: str | None,
    ) -> str:
        manifest = {
            "schema": _SCHEMA_VERSION,
            "provider": _PROVIDER_VERSION,
            "codegraph": _engine_identity(),
            "bounds": {
                "files": self._max_context_files,
                "bytes": self._max_context_bytes,
            },
            "context_error": reason,
            "files": [
                {
                    "path": path,
                    "sha256": (
                        hashlib.sha256(content.encode("utf-8")).hexdigest()
                        if content is not None
                        else "absent"
                    ),
                }
                for path, content in sorted(files.items())
            ],
            "patch_sha256": hashlib.sha256((action.proposed_patch or "").encode("utf-8")).hexdigest(),
            "scope": asdict(scope),
        }
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def manifest_hash(
        self, action: CandidateAction, repo_root: str, scope: GraphRiskScope
    ) -> str:
        files, reason = self._context(action, repo_root, scope)
        return self._manifest_hash(action, scope, files, reason)

    def _cache_path(self, _repo_root: str, manifest_hash: str) -> Path:
        return self._cache_root / "graph_continuity" / "v1" / f"{manifest_hash}.json"

    def _load_cache(self, path: Path) -> CandidateGraphRiskEvidence | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema") != _SCHEMA_VERSION:
                return None
            evidence = raw["evidence"]
            facts = tuple(ScopedGraphRiskFact(**fact) for fact in evidence.get("facts", ()))
            result = CandidateGraphRiskEvidence(
                **{key: value for key, value in evidence.items() if key not in {"facts", "verified_patch_hash", "cache_hit"}},
                facts=facts,
                verified_patch_hash=None,
                cache_hit=True,
            )
            # Positive risk-reducing evidence is never trusted from disk without a host
            # authentication boundary. The disk cache stores only conservative misses/ambiguity.
            if result.status == "available":
                return None
            if any(
                not math.isfinite(fact.confidence)
                or not 0.0 <= fact.confidence <= 1.0
                for fact in result.facts
            ):
                return None
            return result
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _save_cache(self, path: Path, evidence: CandidateGraphRiskEvidence) -> None:
        if evidence.status == "available":
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(evidence)
            payload["verified_patch_hash"] = None
            payload["cache_hit"] = False
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
            ) as handle:
                json.dump({"schema": _SCHEMA_VERSION, "evidence": payload}, handle, sort_keys=True)
                temp = Path(handle.name)
            os.replace(temp, path)
            entries = sorted(path.parent.glob("*.json"), key=lambda item: item.stat().st_mtime)
            total_bytes = sum(item.stat().st_size for item in entries)
            stale_count = max(0, len(entries) - self._max_cache_entries)
            for stale in entries:
                if stale_count <= 0 and total_bytes <= self._max_cache_bytes:
                    break
                size = stale.stat().st_size
                stale.unlink(missing_ok=True)
                stale_count -= 1
                total_bytes -= size
        except OSError:
            return

    @staticmethod
    def _read_nodes(con: sqlite3.Connection) -> list[sqlite3.Row]:
        return con.execute(
            "SELECT id, kind, name, qualified_name, file_path, start_line, end_line, "
            "visibility, is_exported, signature FROM nodes"
        ).fetchall()

    @staticmethod
    def _is_exported(row: sqlite3.Row) -> bool:
        return bool(row["is_exported"]) or str(row["visibility"] or "") in {
            "public", "public_api", "exported"
        }

    def _facts(
        self, before_db: Path, after_db: Path, scope: GraphRiskScope
    ) -> tuple[ScopedGraphRiskFact, ...]:
        before = sqlite3.connect(before_db)
        after = sqlite3.connect(after_db)
        before.row_factory = after.row_factory = sqlite3.Row
        try:
            before_nodes = self._read_nodes(before)
            after_nodes = self._read_nodes(after)
            covered: list[str] = []
            for real_id, file_path, qualified_name in zip(
                scope.owner_node_ids,
                scope.owner_file_paths,
                scope.owner_qualified_names,
                strict=True,
            ):
                old = [
                    row for row in before_nodes
                    if row["file_path"] == file_path
                    and row["qualified_name"] == qualified_name
                    and self._is_exported(row)
                ]
                surviving = [
                    row for row in after_nodes
                    if row["file_path"] == file_path
                    and row["qualified_name"] == qualified_name
                    and self._is_exported(row)
                ]
                if len(old) != 1 or len(surviving) != 1:
                    return ()
                binding = surviving[0]
                if str(binding["kind"]) not in _ALIAS_BINDING_KINDS:
                    return ()
                refs = after.execute(
                    "SELECT e.target, e.metadata, n.kind, n.signature FROM edges e "
                    "JOIN nodes n ON n.id = e.target "
                    "WHERE e.kind IN ('references','calls') "
                    "AND e.line BETWEEN ? AND ? "
                    "AND e.source IN (?, ?)",
                    (binding["start_line"], binding["end_line"], binding["id"], f"file:{file_path}"),
                ).fetchall()
                strong = []
                for ref in refs:
                    try:
                        metadata = json.loads(ref["metadata"] or "{}")
                    except json.JSONDecodeError:
                        continue
                    if (
                        ref["kind"] in _CALLABLE_KINDS
                        and old[0]["kind"] in _CALLABLE_KINDS
                        and bool(old[0]["signature"])
                        and ref["signature"] == old[0]["signature"]
                        and float(metadata.get("confidence", 0.0)) >= 0.90
                        and metadata.get("resolvedBy") not in {None, "heuristic"}
                    ):
                        strong.append(ref)
                if len(strong) != 1:
                    return ()
                before_consumers = before.execute(
                    "SELECT COUNT(DISTINCT source) FROM edges "
                    "WHERE target = ? AND kind IN ('references','calls')",
                    (old[0]["id"],),
                ).fetchone()[0]
                after_consumers = after.execute(
                    "SELECT COUNT(DISTINCT source) FROM edges "
                    "WHERE target = ? AND kind IN ('references','calls')",
                    (binding["id"],),
                ).fetchone()[0]
                if int(after_consumers or 0) < int(before_consumers or 0):
                    return ()
                covered.append(real_id)
            if set(covered) != set(scope.owner_node_ids):
                return ()
            return (
                ScopedGraphRiskFact(
                    fact_kind="exported_binding_continuity",
                    event=scope.event,
                    risk_source=scope.risk_source,
                    owner_node_ids=tuple(sorted(covered)),
                    confidence=0.95,
                ),
            )
        finally:
            before.close()
            after.close()

    def analyze(
        self, action: CandidateAction, repo_root: str, scope: GraphRiskScope
    ) -> CandidateGraphRiskEvidence:
        started = time.monotonic()
        deadline = started + self._max_total_seconds
        if not self._enabled or not action.proposed_patch or not scope.owner_node_ids:
            return CandidateGraphRiskEvidence(status="not_applicable")
        if scope.event not in _SUPPORTED_EVENTS:
            return CandidateGraphRiskEvidence(status="not_applicable")
        if not (
            len(scope.owner_node_ids)
            == len(scope.owner_file_paths)
            == len(scope.owner_qualified_names)
        ):
            return CandidateGraphRiskEvidence(status="unavailable", reason="owner scope is malformed")

        prefilter_started = time.monotonic()
        before, context_error = self._context(action, repo_root, scope)
        prefilter_ms = _elapsed(prefilter_started)
        context_bytes = sum(len(value.encode("utf-8")) for value in before.values() if value is not None)
        if context_error:
            return CandidateGraphRiskEvidence(
                status="unavailable",
                reason=context_error,
                context_file_count=len(before),
                context_bytes=context_bytes,
                context_truncated="cap exceeded" in context_error,
                prefilter_latency_ms=prefilter_ms,
                total_latency_ms=_elapsed(started),
            )
        if time.monotonic() >= deadline:
            return CandidateGraphRiskEvidence(
                status="unavailable", reason="materialized graph deadline exhausted",
                total_latency_ms=_elapsed(started),
            )
        materialize_started = time.monotonic()
        materialize_before = {
            path: content.replace("\r\n", "\n") if content is not None else None
            for path, content in before.items()
        }
        remaining = max(0.1, deadline - time.monotonic())
        after_files = materialize_patch(
            materialize_before,
            action.proposed_patch,
            timeout_seconds=max(0.1, min(10.0, remaining / 4.0)),
        )
        materialize_ms = _elapsed(materialize_started)
        if after_files is None:
            return CandidateGraphRiskEvidence(
                status="unavailable", reason="candidate patch did not apply cleanly",
                materialize_latency_ms=materialize_ms, total_latency_ms=_elapsed(started),
            )
        for file_path, qualified_name in zip(
            scope.owner_file_paths, scope.owner_qualified_names, strict=True
        ):
            leaf = qualified_name.replace("::", ".").split(".")[-1]
            if leaf not in (after_files.get(file_path) or ""):
                return CandidateGraphRiskEvidence(
                    status="ambiguous",
                    reason="old public name does not reappear in candidate",
                    context_file_count=len(before), context_bytes=context_bytes,
                    prefilter_latency_ms=prefilter_ms,
                    materialize_latency_ms=materialize_ms, total_latency_ms=_elapsed(started),
                )

        manifest_hash = self._manifest_hash(action, scope, before, context_error)
        cache_path = self._cache_path(repo_root, manifest_hash)
        cached = self._load_cache(cache_path)
        if cached is not None:
            return cached

        index_started = time.monotonic()
        query_ms = 0.0
        try:
            with tempfile.TemporaryDirectory(prefix="pebra-continuity-") as temp_dir:
                workspace = Path(temp_dir)
                root = workspace / "repo"
                root.mkdir()
                _write_files(root, before)
                before_db = self._indexer(root)
                if time.monotonic() >= deadline:
                    raise TimeoutError("materialized graph deadline exhausted")
                before_copy = workspace / "before.db"
                shutil.copy2(before_db, before_copy)
                _clear(root)
                _write_files(root, after_files)
                after_db = self._indexer(root)
                if time.monotonic() >= deadline:
                    raise TimeoutError("materialized graph deadline exhausted")
                index_ms = _elapsed(index_started)
                query_started = time.monotonic()
                facts = self._facts(before_copy, after_db, scope)
                query_ms = _elapsed(query_started)
        except (OSError, sqlite3.Error, subprocess.SubprocessError, TimeoutError, ValueError, TypeError):
            return CandidateGraphRiskEvidence(
                status="unavailable", reason="materialized CodeGraph continuity unavailable",
                context_file_count=len(before), context_bytes=context_bytes,
                prefilter_latency_ms=prefilter_ms, materialize_latency_ms=materialize_ms,
                index_latency_ms=_elapsed(index_started), query_latency_ms=query_ms,
                total_latency_ms=_elapsed(started), manifest_hash=manifest_hash,
            )
        evidence = CandidateGraphRiskEvidence(
            status="available" if facts else "ambiguous",
            facts=facts,
            provider="materialized_codegraph",
            reason=None if facts else "structural continuity was not established",
            manifest_hash=manifest_hash,
            context_file_count=len(before),
            context_bytes=context_bytes,
            prefilter_latency_ms=prefilter_ms,
            materialize_latency_ms=materialize_ms,
            index_latency_ms=index_ms,
            query_latency_ms=query_ms,
            total_latency_ms=_elapsed(started),
        )
        self._save_cache(cache_path, evidence)
        return evidence
