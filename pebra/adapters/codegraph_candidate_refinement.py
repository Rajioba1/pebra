"""Bounded materialized CodeGraph continuity evidence for revised candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
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
_CACHE_VERSION = 7
_PROVIDER_VERSION = f"materialized-continuity-v{_CACHE_VERSION}"
_CALLABLE_KINDS = {"function", "method", "class", "struct", "interface", "trait", "protocol"}
_ALIAS_BINDING_KINDS = {"constant", "variable"}
_SUPPORTED_EVENTS = {"public_api_break"}
_CONTINUITY_EDGE_KINDS = ("calls", "instantiates", "references")
_CONFIG_NAMES = (
    "package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
)

Indexer = Callable[[Path], Path]
ContextFiles = Callable[[str, GraphRiskScope], tuple[str, ...]]


def _elapsed(start: float) -> float:
    return max(0.0, (time.monotonic() - start) * 1000.0)


def _default_cache_root() -> Path:
    explicit = os.environ.get("PEBRA_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "pebra" / "cache"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "pebra"
    return Path.home() / ".cache" / "pebra"


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


def _default_context_files(_repo_root: str, _scope: GraphRiskScope) -> tuple[str, ...]:
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
        # ``.pebra`` is a repository-root marker. A user-level cache there would make every
        # descendant of the home directory resolve as one giant repository.
        self._cache_root = cache_root or _default_cache_root()

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
            expanded = tuple(self._context_files_fn(repo_root, scope))
            if len(candidates | set(expanded)) > self._max_context_files:
                return {}, "context file cap exceeded"
            candidates.update(expanded)
        except Exception:  # noqa: BLE001 - graph incompleteness cannot earn risk-reducing evidence
            return {}, "dependent graph context unavailable"
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
                if target.is_file():
                    total += target.stat().st_size
                    if total > self._max_context_bytes:
                        return files, "context byte cap exceeded"
                content = target.read_bytes().decode("utf-8") if target.is_file() else None
                files[rel] = content
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
        return (
            self._cache_root
            / "graph_continuity"
            / f"v{_CACHE_VERSION}"
            / f"{manifest_hash}.json"
        )

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
            "start_column, end_column, visibility, is_exported, signature FROM nodes"
        ).fetchall()

    @staticmethod
    def _node_source(files: Mapping[str, str | None], row: sqlite3.Row) -> str | None:
        content = files.get(str(row["file_path"]))
        if content is None:
            return None
        lines = content.splitlines(keepends=True)
        start_line = int(row["start_line"]) - 1
        end_line = int(row["end_line"]) - 1
        if start_line < 0 or end_line < start_line or end_line >= len(lines):
            return None
        start_column = max(0, int(row["start_column"] or 0))
        end_column = max(0, int(row["end_column"] or 0))
        if start_line == end_line:
            return lines[start_line][start_column:end_column]
        selected = [lines[start_line][start_column:], *lines[start_line + 1:end_line]]
        selected.append(lines[end_line][:end_column])
        return "".join(selected)

    @classmethod
    def _same_implementation(
        cls,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        old: sqlite3.Row,
        target: sqlite3.Row,
    ) -> bool:
        old_source = cls._node_source(before_files, old)
        target_source = cls._node_source(after_files, target)
        old_name = str(old["name"] or "")
        target_name = str(target["name"] or "")
        if not old_source or not target_source or not old_name or not target_name:
            return False
        def canonical(source: str, name: str) -> str | None:
            normalized = source.replace("\r\n", "\n")
            pattern = re.compile(rf"\bfunction\s+{re.escape(name)}\b")
            matches = list(pattern.finditer(normalized))
            if len(matches) != 1:
                return None
            match = matches[0]
            name_start = match.end() - len(name)
            return (
                normalized[:name_start]
                + "__PEBRA_RENAMED__"
                + normalized[match.end():]
            )

        canonical_old = canonical(old_source, old_name)
        canonical_target = canonical(target_source, target_name)
        if canonical_old is None or canonical_target is None:
            return False
        return canonical_old == canonical_target

    @staticmethod
    def _is_exported(row: sqlite3.Row) -> bool:
        return bool(row["is_exported"]) or str(row["visibility"] or "") in {
            "public", "public_api", "exported"
        }

    @staticmethod
    def _trusted_edge_confidence(row: sqlite3.Row) -> float | None:
        try:
            metadata = json.loads(row["metadata"] or "{}")
            confidence = float(metadata.get("confidence", 0.0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if (
            not math.isfinite(confidence)
            or confidence < 0.90
            or metadata.get("resolvedBy") in {None, "heuristic"}
        ):
            return None
        return min(1.0, confidence)

    @classmethod
    def _continuity_edge_metadata(
        cls, row: sqlite3.Row
    ) -> tuple[str, float, bool] | None:
        """Return stable envelope metadata and whether it may raise proof confidence."""
        try:
            metadata = json.loads(row["metadata"] or "{}")
            confidence = float(metadata.get("confidence", 0.0))
            resolver = str(metadata.get("resolvedBy") or "")
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not math.isfinite(confidence) or resolver in {"", "heuristic"}:
            return None
        trusted = cls._trusted_edge_confidence(row)
        if trusted is not None:
            return resolver, trusted, True
        if resolver == "exact-match" and confidence >= 0.70:
            return resolver, min(1.0, confidence), False
        return None

    @classmethod
    def _binding_target(
        cls,
        con: sqlite3.Connection,
        nodes: list[sqlite3.Row],
        binding: sqlite3.Row,
    ) -> tuple[sqlite3.Row, float] | None:
        refs = con.execute(
            "SELECT e.target, e.metadata, n.kind, n.signature FROM edges e "
            "JOIN nodes n ON n.id = e.target "
            "WHERE e.kind IN ('references','calls') "
            "AND e.line BETWEEN ? AND ? "
            "AND e.source IN (?, ?)",
            (
                binding["start_line"], binding["end_line"], binding["id"],
                f"file:{binding['file_path']}",
            ),
        ).fetchall()
        strong = []
        for ref in refs:
            confidence = cls._trusted_edge_confidence(ref)
            if (
                ref["kind"] in _CALLABLE_KINDS
                and confidence is not None
            ):
                strong.append((ref, confidence))
        if len(strong) != 1:
            return None
        ref, confidence = strong[0]
        target = next((row for row in nodes if row["id"] == ref["target"]), None)
        return (target, confidence) if target is not None else None

    @classmethod
    def _canonical_identifier_source(
        cls, source: str, name: str
    ) -> tuple[str, int] | None:
        """Replace code identifiers only; literals, comments, and property names fail closed."""
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) is None:
            return None
        out: list[str] = []
        count = 0
        index = 0
        length = len(source)
        while index < length:
            char = source[index]
            if char in {"'", '"', "`"}:
                quote = char
                end = index + 1
                escaped = False
                while end < length:
                    current = source[end]
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == quote:
                        end += 1
                        break
                    end += 1
                segment = source[index:end]
                if re.search(rf"\b{re.escape(name)}\b", segment):
                    return None
                out.append(segment)
                index = end
                continue
            if source.startswith("//", index):
                end = source.find("\n", index)
                end = length if end < 0 else end
                segment = source[index:end]
                if re.search(rf"\b{re.escape(name)}\b", segment):
                    return None
                out.append(segment)
                index = end
                continue
            if source.startswith("/*", index):
                end = source.find("*/", index + 2)
                end = length if end < 0 else end + 2
                segment = source[index:end]
                if re.search(rf"\b{re.escape(name)}\b", segment):
                    return None
                out.append(segment)
                index = end
                continue
            if char == "/":
                # Distinguishing division from a JavaScript/TypeScript regex literal requires a real
                # parser. This proof must under-fire rather than rewrite identifiers inside regexes.
                return None
            if char.isalpha() or char in {"_", "$"}:
                end = index + 1
                while end < length and (source[end].isalnum() or source[end] in {"_", "$"}):
                    end += 1
                token = source[index:end]
                if token == name:
                    previous = next(
                        (source[pos] for pos in range(index - 1, -1, -1) if not source[pos].isspace()),
                        "",
                    )
                    following = next(
                        (source[pos] for pos in range(end, length) if not source[pos].isspace()),
                        "",
                    )
                    if previous == "." or following == ":":
                        return None
                    out.append("__PEBRA_BINDING__")
                    count += 1
                else:
                    out.append(token)
                index = end
                continue
            out.append(char)
            index += 1
        return "".join(out).replace("\r\n", "\n"), count

    @classmethod
    def _identifier_only_migration(
        cls, before_source: str, after_source: str, old_name: str, target_name: str
    ) -> bool:
        before = cls._canonical_identifier_source(before_source, old_name)
        after = cls._canonical_identifier_source(after_source, target_name)
        return (
            before is not None
            and after is not None
            and before[1] > 0
            and before[1] == after[1]
            and before[0] == after[0]
        )

    @classmethod
    def _patch_is_exhaustive_direct_alias(
        cls, patch: str, old_name: str, target_name: str
    ) -> bool:
        """Prove every changed line is the identifier migration or one exact direct alias."""
        alias = re.compile(
            rf"\s*export\s+const\s+{re.escape(old_name)}\s*=\s*"
            rf"{re.escape(target_name)}\s*;\s*"
        )
        # Deliberately aggregate every hunk in every touched file. A helper rebinding anywhere in
        # the candidate invalidates the proof; narrowing this to the declaration file would reopen
        # a false-continuity path for functions that close over changed free identifiers.
        removed: list[str] = []
        added: list[str] = []
        alias_count = 0
        saw_block = False
        block_has_hunk = False
        in_hunk = False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                if saw_block and not block_has_hunk:
                    return False
                saw_block = True
                block_has_hunk = False
                in_hunk = False
                continue
            if line.startswith("@@ "):
                if not saw_block:
                    return False
                block_has_hunk = True
                in_hunk = True
                continue
            if not in_hunk:
                if line.startswith(("index ", "--- ", "+++ ")) or not line.strip():
                    continue
                return False
            if line.startswith("\\ No newline at end of file") or line.startswith(" "):
                continue
            if line.startswith(("--- ", "+++ ")):
                continue
            if line.startswith("-"):
                candidate = line[1:]
                if candidate.strip():
                    removed.append(candidate)
            elif line.startswith("+"):
                candidate = line[1:]
                if alias.fullmatch(candidate):
                    alias_count += 1
                elif candidate.strip():
                    added.append(candidate)
            elif line:
                return False
        before = cls._canonical_identifier_source("\n".join(removed), old_name)
        after = cls._canonical_identifier_source("\n".join(added), target_name)
        return (
            saw_block
            and block_has_hunk
            and alias_count == 1
            and before is not None
            and after is not None
            and before[1] > 0
            and before[1] == after[1]
            and before[0] == after[0]
        )

    @classmethod
    def _same_external_source(
        cls,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        before_node: sqlite3.Row | None,
        after_node: sqlite3.Row | None,
        source_id: str,
        old_name: str,
        target_name: str,
    ) -> bool:
        if source_id.startswith("file:"):
            path = source_id.removeprefix("file:")
            before_source = before_files.get(path)
            after_source = after_files.get(path)
        else:
            if before_node is None or after_node is None:
                return False
            before_source = cls._node_source(before_files, before_node)
            after_source = cls._node_source(after_files, after_node)
        if before_source is None or after_source is None:
            return False
        before_normalized = before_source.replace("\r\n", "\n")
        after_normalized = after_source.replace("\r\n", "\n")
        if before_normalized == after_normalized:
            return True
        return cls._identifier_only_migration(
            before_normalized, after_normalized, old_name, target_name
        )

    @classmethod
    def _same_reference_migration(
        cls,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        old: sqlite3.Row,
        surviving: sqlite3.Row,
        old_name: str,
        target_name: str,
    ) -> bool:
        if old["kind"] != surviving["kind"] or old["signature"] != surviving["signature"]:
            return False
        before_source = cls._node_source(before_files, old)
        after_source = cls._node_source(after_files, surviving)
        if not before_source or not after_source:
            return False
        return cls._identifier_only_migration(
            before_source, after_source, old_name, target_name
        )

    def _facts(
        self,
        before_db: Path,
        after_db: Path,
        scope: GraphRiskScope,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        patch: str,
    ) -> tuple[ScopedGraphRiskFact, ...]:
        before = sqlite3.connect(before_db)
        after = sqlite3.connect(after_db)
        before.row_factory = after.row_factory = sqlite3.Row
        try:
            before_nodes = self._read_nodes(before)
            after_nodes = self._read_nodes(after)
            owners: list[tuple[str, sqlite3.Row, list[sqlite3.Row]]] = []
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
                ]
                surviving = [
                    row for row in after_nodes
                    if row["file_path"] == file_path
                    and row["qualified_name"] == qualified_name
                ]
                if len(old) != 1 or len(surviving) != 1:
                    return ()
                owners.append((real_id, old[0], surviving))

            alias_owners = [
                (real_id, old, surviving[0])
                for real_id, old, surviving in owners
                if str(surviving[0]["kind"]) in _ALIAS_BINDING_KINDS
                and self._is_exported(old)
                and self._is_exported(surviving[0])
            ]
            if len(alias_owners) != 1:
                return ()
            alias_real_id, renamed_old, binding = alias_owners[0]
            binding_target = self._binding_target(after, after_nodes, binding)
            if (
                binding_target is None
                or renamed_old["kind"] not in _CALLABLE_KINDS
                or not bool(renamed_old["signature"])
            ):
                return ()
            target, binding_confidence = binding_target
            if (
                target["signature"] != renamed_old["signature"]
                or not self._same_implementation(before_files, after_files, renamed_old, target)
            ):
                return ()

            covered = [alias_real_id]
            edge_confidences = [binding_confidence]
            after_targets_by_old_id: dict[str, tuple[str, ...]] = {
                str(renamed_old["id"]): (str(binding["id"]), str(target["id"]))
            }
            old_name = str(renamed_old["name"] or "")
            target_name = str(target["name"] or "")
            if not self._patch_is_exhaustive_direct_alias(
                patch, old_name, target_name
            ):
                return ()
            for real_id, old, surviving in owners:
                if real_id == alias_real_id:
                    continue
                current = surviving[0]
                if not self._same_reference_migration(
                    before_files, after_files, old, current, old_name, target_name
                ):
                    return ()
                before_edges = before.execute(
                    "SELECT kind, metadata FROM edges WHERE source = ? AND target = ? "
                    "AND kind IN (?,?,?)",
                    (old["id"], renamed_old["id"], *_CONTINUITY_EDGE_KINDS),
                ).fetchall()
                after_edges = after.execute(
                    "SELECT kind, metadata FROM edges WHERE source = ? AND target = ? "
                    "AND kind IN (?,?,?)",
                    (current["id"], target["id"], *_CONTINUITY_EDGE_KINDS),
                ).fetchall()
                before_confidences = [self._trusted_edge_confidence(edge) for edge in before_edges]
                after_confidences = [self._trusted_edge_confidence(edge) for edge in after_edges]
                if (
                    not before_edges
                    or Counter(str(edge["kind"]) for edge in before_edges)
                    != Counter(str(edge["kind"]) for edge in after_edges)
                    or any(value is None for value in (*before_confidences, *after_confidences))
                ):
                    return ()
                edge_confidences.extend(
                    value for value in (*before_confidences, *after_confidences) if value is not None
                )
                covered.append(real_id)
                after_targets_by_old_id[str(old["id"])] = (str(current["id"]),)

            old_owner_ids = {str(old["id"]) for _, old, _ in owners}
            edge_kinds = _CONTINUITY_EDGE_KINDS
            callers = before.execute(
                "SELECT source, target, kind, metadata FROM edges WHERE target IN ("
                + ",".join("?" for _ in old_owner_ids)
                + ") "
                "AND kind IN (?, ?, ?) AND source NOT IN ("
                + ",".join("?" for _ in old_owner_ids)
                + ")",
                (*sorted(old_owner_ids), *edge_kinds, *sorted(old_owner_ids)),
            ).fetchall()
            if len({str(caller["source"]) for caller in callers}) != scope.expected_consumer_count:
                return ()

            before_by_id = {str(row["id"]): row for row in before_nodes}
            caller_groups = Counter(
                (str(caller["source"]), str(caller["target"]), str(caller["kind"]))
                for caller in callers
            )
            for (source, old_target, edge_kind), expected_count in caller_groups.items():
                before_group = [
                    caller
                    for caller in callers
                    if (
                        str(caller["source"]),
                        str(caller["target"]),
                        str(caller["kind"]),
                    )
                    == (source, old_target, edge_kind)
                ]
                before_metadata = [
                    self._continuity_edge_metadata(edge) for edge in before_group
                ]
                if any(item is None for item in before_metadata):
                    return ()
                old_source = None
                current_source = None
                if source.startswith("file:"):
                    after_source = source
                else:
                    old_source = before_by_id.get(source)
                    if old_source is None:
                        return ()
                    matches = [
                        row for row in after_nodes
                        if row["file_path"] == old_source["file_path"]
                        and row["qualified_name"] == old_source["qualified_name"]
                        and row["kind"] == old_source["kind"]
                    ]
                    if len(matches) != 1:
                        return ()
                    current_source = matches[0]
                    after_source = str(current_source["id"])
                if not self._same_external_source(
                    before_files,
                    after_files,
                    old_source,
                    current_source,
                    source,
                    old_name,
                    target_name,
                ):
                    return ()
                after_targets = after_targets_by_old_id.get(old_target)
                if not after_targets:
                    return ()
                continuity = after.execute(
                    "SELECT metadata FROM edges WHERE source = ? AND target IN ("
                    + ",".join("?" for _ in after_targets)
                    + ") AND kind = ?",
                    (after_source, *after_targets, edge_kind),
                ).fetchall()
                continuity_metadata = [
                    self._continuity_edge_metadata(edge) for edge in continuity
                ]
                if (
                    len(continuity) != expected_count
                    or any(item is None for item in continuity_metadata)
                    or Counter(before_metadata) != Counter(continuity_metadata)
                ):
                    return ()
                edge_confidences.extend(
                    confidence
                    for item in continuity_metadata
                    if item is not None
                    for _resolver, confidence, proof_bearing in (item,)
                    if proof_bearing
                )
            return (
                ScopedGraphRiskFact(
                    fact_kind="exported_binding_continuity",
                    event=scope.event,
                    risk_source=scope.risk_source,
                    owner_node_ids=tuple(sorted(covered)),
                    confidence=min(edge_confidences),
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
                retryable_infrastructure=context_error in {
                    "dependent graph context unavailable",
                    "context file could not be read",
                },
                context_file_count=len(before),
                context_bytes=context_bytes,
                context_truncated="cap exceeded" in context_error,
                prefilter_latency_ms=prefilter_ms,
                total_latency_ms=_elapsed(started),
            )
        if time.monotonic() >= deadline:
            return CandidateGraphRiskEvidence(
                status="unavailable", reason="materialized graph deadline exhausted",
                retryable_infrastructure=True,
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
        with tempfile.TemporaryDirectory(prefix="pebra-continuity-") as temp_dir:
            workspace = Path(temp_dir)
            root = workspace / "repo"
            root.mkdir()
            _write_files(root, before)
            try:
                before_db = self._indexer(root)
                if time.monotonic() >= deadline:
                    raise TimeoutError("materialized graph deadline exhausted")
                before_copy = workspace / "before.db"
                shutil.copy2(before_db, before_copy)
            except (
                OSError, sqlite3.Error, subprocess.SubprocessError,
                TimeoutError, ValueError, TypeError,
            ) as exc:
                return CandidateGraphRiskEvidence(
                    status="unavailable",
                    reason="before-snapshot CodeGraph unavailable",
                    retryable_infrastructure=isinstance(
                        exc, (OSError, sqlite3.Error, subprocess.SubprocessError, TimeoutError)
                    ),
                    context_file_count=len(before), context_bytes=context_bytes,
                    prefilter_latency_ms=prefilter_ms, materialize_latency_ms=materialize_ms,
                    index_latency_ms=_elapsed(index_started), total_latency_ms=_elapsed(started),
                    manifest_hash=manifest_hash,
                )
            try:
                _clear(root)
                _write_files(root, after_files)
                after_db = self._indexer(root)
                if time.monotonic() >= deadline:
                    raise TimeoutError("materialized graph deadline exhausted")
                index_ms = _elapsed(index_started)
                query_started = time.monotonic()
                facts = self._facts(
                    before_copy,
                    after_db,
                    scope,
                    before,
                    after_files,
                    action.proposed_patch,
                )
                query_ms = _elapsed(query_started)
            except (
                OSError, sqlite3.Error, subprocess.SubprocessError,
                TimeoutError, ValueError, TypeError,
            ) as exc:
                return CandidateGraphRiskEvidence(
                    status="unavailable",
                    reason="candidate after-graph unavailable",
                    retryable_infrastructure=isinstance(
                        exc, (OSError, sqlite3.Error)
                    ),
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
