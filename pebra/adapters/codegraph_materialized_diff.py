"""Dark-gated before/after CodeGraph semantic diff.

This adapter is deliberately dormant: callers must opt in with ``enabled=True``. It materializes only
the touched files into tiny temp worktrees, indexes each with CodeGraph, then compares owner metadata
by stable ``(file_path, qualified_name)`` keys. It never treats missing metadata as proof of no change.
"""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine
from pebra.core.models import MaterializedGraphDiffResult, MaterializedGraphDiffRow

_CALLABLE_KINDS = ("function", "method", "class", "struct", "interface", "trait", "protocol")


Indexer = Callable[[Path], Path]


class CodeGraphMaterializedDiffAdapter:
    """Compare before/after CodeGraph metadata for touched files only.

    The default constructor is safe: disabled, no subprocesses. Tests can inject ``indexer`` to avoid
    invoking CodeGraph while still exercising DB reading and comparison rules.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        indexer: Indexer | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._enabled = enabled
        self._indexer = indexer or self._index_with_codegraph
        self._timeout_s = timeout_s

    def diff(
        self,
        *,
        before_files: Mapping[str, str | None],
        after_files: Mapping[str, str | None],
        repo_root: str,
    ) -> MaterializedGraphDiffResult:
        start = time.monotonic()
        if not self._enabled:
            return MaterializedGraphDiffResult(
                fallback_reason="materialized CodeGraph diff disabled",
            )
        try:
            paths = _validate_materialized_paths(set(before_files) | set(after_files))
        except ValueError:
            return MaterializedGraphDiffResult(
                fallback_reason="invalid materialized file path",
                latency_ms=_elapsed_ms(start),
            )
        if not paths:
            return MaterializedGraphDiffResult(
                fallback_reason="no files to materialize",
                latency_ms=_elapsed_ms(start),
            )
        try:
            with tempfile.TemporaryDirectory(prefix="pebra-cg-before-") as before_tmp, (
                tempfile.TemporaryDirectory(prefix="pebra-cg-after-")
            ) as after_tmp:
                before_root = Path(before_tmp)
                after_root = Path(after_tmp)
                _write_files(before_root, before_files)
                _write_files(after_root, after_files)
                before_nodes = _read_nodes(self._indexer(before_root))
                after_nodes = _read_nodes(self._indexer(after_root))
        except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError) as exc:
            if str(exc) == "materialized owner key ambiguous":
                return MaterializedGraphDiffResult(
                    fallback_reason="materialized owner key ambiguous",
                    latency_ms=_elapsed_ms(start),
                )
            return MaterializedGraphDiffResult(
                fallback_reason=f"materialized graph unavailable: {type(exc).__name__}",
                latency_ms=_elapsed_ms(start),
            )

        before_keys = set(before_nodes)
        after_keys = set(after_nodes)
        if before_keys != after_keys:
            return MaterializedGraphDiffResult(
                fallback_reason="materialized owner mismatch",
                latency_ms=_elapsed_ms(start),
            )

        rows: list[MaterializedGraphDiffRow] = []
        for key in sorted(before_keys):
            before = before_nodes[key]
            after = after_nodes[key]
            changes = _compare_semantic_fields(before, after)
            if not changes:
                continue
            rows.append(MaterializedGraphDiffRow(
                file_path=key[0],
                qualified_name=key[1],
                language=str(after.get("language") or before.get("language") or "unknown"),
                **changes,
            ))
        if not rows:
            return MaterializedGraphDiffResult(
                fallback_reason="no comparable semantic fields",
                latency_ms=_elapsed_ms(start),
            )
        return MaterializedGraphDiffResult(
            available=True,
            rows=tuple(rows),
            latency_ms=_elapsed_ms(start),
        )

    def _index_with_codegraph(self, root: Path) -> Path:
        exe = find_engine()
        if exe is None:
            raise FileNotFoundError("codegraph")
        proc = subprocess.run(
            resolve_engine_argv(exe, ["init", str(root)]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            raise subprocess.SubprocessError("codegraph init failed")
        db_path = root / ".codegraph" / "codegraph.db"
        if not db_path.is_file():
            raise FileNotFoundError(str(db_path))
        return db_path


def _elapsed_ms(start: float) -> float:
    return max(0.0, (time.monotonic() - start) * 1000.0)


def _validate_materialized_paths(paths: set[str]) -> tuple[str, ...]:
    clean: list[str] = []
    for rel in paths:
        normalized = rel.replace("\\", "/")
        posix = PurePosixPath(normalized)
        win = PureWindowsPath(rel)
        if (
            not normalized
            or posix.is_absolute()
            or win.is_absolute()
            or win.drive
            or ".." in posix.parts
        ):
            raise ValueError("invalid materialized file path")
        clean.append(posix.as_posix())
    return tuple(sorted(clean))


def _write_files(root: Path, files: Mapping[str, str | None]) -> None:
    root_real = root.resolve()
    for rel, content in files.items():
        if content is None:
            continue
        normalized = _validate_materialized_paths({rel})[0]
        path = (root / normalized).resolve()
        path.relative_to(root_real)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _read_nodes(db_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        ph = ",".join("?" * len(_CALLABLE_KINDS))
        rows = con.execute(
            f"SELECT file_path, qualified_name, language, signature, visibility, return_type "
            f"FROM nodes WHERE kind IN ({ph}) AND file_path IS NOT NULL "
            f"AND qualified_name IS NOT NULL",
            _CALLABLE_KINDS,
        ).fetchall()
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            key = (str(r["file_path"]).replace("\\", "/"), str(r["qualified_name"]))
            if key in out:
                raise ValueError("materialized owner key ambiguous")
            out[key] = dict(r)
        return out
    finally:
        con.close()


def _compare_semantic_fields(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    for field, changed_key in (
        ("signature", "signature_changed"),
        ("return_type", "return_type_changed"),
        ("visibility", "visibility_changed"),
    ):
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value is None or after_value is None:
            out[changed_key] = None
            continue
        out[changed_key] = before_value != after_value
    if out.get("signature_changed") is None:
        return {}
    return out
