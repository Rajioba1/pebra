"""Dark-gated before/after CodeGraph semantic diff.

This adapter is deliberately dormant: callers must opt in with ``enabled=True``. It materializes only
the touched files into tiny temp worktrees, indexes each with CodeGraph, then compares owner metadata
by stable ``(file_path, qualified_name)`` keys. It never treats missing metadata as proof of no change.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from pebra.adapters import patch_header_adapter
from pebra.adapters._paths import is_safe_relative, safe_relative_files
from pebra.adapters.codegraph_temp_index import index_temp_tree
from pebra.adapters.patch_materializer import materialize_patch
from pebra.core.language_capability import derive_visibility_from_export
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
            paths = _validate_materialized_paths(repo_root, set(before_files) | set(after_files))
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
            # BUG-6: reuse ONE temp dir for before and after so both indexes share the identical
            # absolute path prefix. This structurally prevents any path-derived component of a
            # qualified_name/signature from making before/after spuriously differ (vs. two random dirs).
            with tempfile.TemporaryDirectory(prefix="pebra-cg-") as tmp:
                root = Path(tmp)
                _write_files(root, before_files)
                before_nodes = _read_nodes(self._indexer(root))
                if not _clear_tree(root):
                    return MaterializedGraphDiffResult(
                        fallback_reason="could not clear materialized CodeGraph scratch tree",
                        latency_ms=_elapsed_ms(start),
                    )
                _write_files(root, after_files)
                after_nodes = _read_nodes(self._indexer(root))
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
        rows: list[MaterializedGraphDiffRow] = []
        for key in sorted(before_keys & after_keys):
            before = before_nodes[key]
            after = after_nodes[key]
            changes = _compare_semantic_fields(before, after)
            if not changes:
                continue
            rows.append(MaterializedGraphDiffRow(
                file_path=key[0],
                qualified_name=key[1],
                language=str(after.get("language") or before.get("language") or "unknown"),
                operation="modified",
                kind=str(after.get("kind") or before.get("kind") or "") or None,
                **changes,
            ))
        for key in sorted(after_keys - before_keys):
            after = after_nodes[key]
            rows.append(MaterializedGraphDiffRow(
                file_path=key[0],
                qualified_name=key[1],
                language=str(after.get("language") or "unknown"),
                operation="added",
                kind=str(after.get("kind") or "") or None,
                is_abstract=_truthy_or_none(after.get("is_abstract")),
            ))
        for key in sorted(before_keys - after_keys):
            before = before_nodes[key]
            rows.append(MaterializedGraphDiffRow(
                file_path=key[0],
                qualified_name=key[1],
                language=str(before.get("language") or "unknown"),
                operation="removed",
                kind=str(before.get("kind") or "") or None,
                is_abstract=_truthy_or_none(before.get("is_abstract")),
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

    def diff_for_patch(self, *, repo_root: str, patch: str) -> MaterializedGraphDiffResult:
        """Assess-path entrypoint: read the CURRENT working-tree content of the patch's touched files
        (the before), apply the patch verbatim to derive the after, and diff. The before side is the
        working tree — NOT HEAD — because the candidate narrows relative to what is on disk right now.
        Fail-closed: disabled / no touched files / non-clean apply all return an unavailable result."""
        if not self._enabled:
            return MaterializedGraphDiffResult(fallback_reason="materialized CodeGraph diff disabled")
        touched = patch_header_adapter.touched_files(patch)
        if not touched:
            return MaterializedGraphDiffResult(fallback_reason="no files touched by patch")
        # The patch headers are untrusted input: reject absolute/.. paths BEFORE reading repo_root, so a
        # malformed candidate can't read arbitrary files outside the repo (safe_relative_files also
        # rejects symlink escapes by resolving against repo_root).
        safe = safe_relative_files(repo_root, list(touched))
        if len(safe) != len(touched):
            return MaterializedGraphDiffResult(fallback_reason="invalid patch file path")
        root = Path(repo_root)
        before: dict[str, str | None] = {}
        for rel in touched:
            fp = root / rel
            try:
                before[rel] = fp.read_text(encoding="utf-8", errors="replace") if fp.is_file() else None
            except OSError:
                before[rel] = None
        after = materialize_patch(before, patch)
        if after is None:
            return MaterializedGraphDiffResult(
                fallback_reason="candidate patch did not apply cleanly to the current working tree"
            )
        return self.diff(before_files=before, after_files=after, repo_root=repo_root)

    def _index_with_codegraph(self, root: Path) -> Path:
        return index_temp_tree(root, timeout_s=self._timeout_s)


def _clear_tree(root: Path) -> bool:
    """Remove every child of ``root`` (files + the .codegraph index dir) so the after-index is built on
    a clean tree in the SAME dir the before-index used (BUG-6)."""
    for child in root.iterdir():
        if child.is_dir():
            try:
                shutil.rmtree(child)
            except OSError:
                return False
        else:
            try:
                child.unlink()
            except OSError:
                return False
    return True


def _elapsed_ms(start: float) -> float:
    return max(0.0, (time.monotonic() - start) * 1000.0)


def _validate_materialized_paths(repo_root: str, paths: set[str]) -> tuple[str, ...]:
    """Repo-safety gate for the touched-file set, applied ONCE per diff. Raises on any path that fails
    the shared canonical predicate (absolute/.., drive, ``:``/ADS, symlink-escape); returns the safe
    paths normalized to posix. `_write_files` relies on this having run and only guards temp-tree
    containment mechanically, so repo-safety lives in exactly one predicate (`_paths.is_safe_relative`).
    """
    clean: list[str] = []
    for rel in paths:
        if not is_safe_relative(repo_root, rel):
            raise ValueError("invalid materialized file path")
        clean.append(PurePosixPath(rel.replace("\\", "/")).as_posix())
    return tuple(sorted(clean))


def _write_files(root: Path, files: Mapping[str, str | None]) -> None:
    # Keys were already repo-safety-validated by `_validate_materialized_paths`; here we only normalize
    # and mechanically guard that the write stays inside the temp tree (defense-in-depth: any escaping
    # key raises ValueError, caught by diff()'s fail-closed handler).
    root_real = root.resolve()
    for rel, content in files.items():
        if content is None:
            continue
        normalized = PurePosixPath(rel.replace("\\", "/")).as_posix()
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
            f"SELECT file_path, qualified_name, language, kind, signature, visibility, return_type, "
            f"is_exported, is_abstract FROM nodes WHERE kind IN ({ph}) AND file_path IS NOT NULL "
            f"AND qualified_name IS NOT NULL",
            _CALLABLE_KINDS,
        ).fetchall()
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            key = (str(r["file_path"]).replace("\\", "/"), str(r["qualified_name"]))
            if key in out:
                raise ValueError("materialized owner key ambiguous")
            d = dict(r)
            # For export-as-visibility languages (Go/JS/JSX) the graph carries no real visibility; derive
            # it from is_exported so an export<->unexport flip surfaces as a visibility change. Null-only
            # and language-gated: a real emitted visibility (TS/C#/...) passes through untouched.
            d["visibility"] = derive_visibility_from_export(
                d.get("language"), d.get("visibility"), d.get("is_exported")
            )
            out[key] = d
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
        ("is_abstract", "is_abstract_changed"),
    ):
        if field == "is_abstract":
            before_value = _truthy_or_none(before.get(field))
            after_value = _truthy_or_none(after.get(field))
        else:
            before_value = before.get(field)
            after_value = after.get(field)
        if before_value is None or after_value is None:
            out[changed_key] = None
            continue
        out[changed_key] = before_value != after_value
    # BUG-4: emit the row when ANY field was comparable (not just signature). A signature-poor owner
    # (signature NULL both sides) with a real visibility/return-type change must NOT be discarded —
    # gating on signature alone silently threw away visibility_changed for partial-signature owners.
    if not any(value is not None for value in out.values()):
        return {}
    return out


def _truthy_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return None
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None
