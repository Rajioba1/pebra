"""codegraph_adapter (M5c.5) — language-agnostic per-symbol fan-in over codegraph's graph.

The robust cross-language contract: PEBRA never guesses a symbol name. It takes the proposed patch,
extracts the OLD-SIDE changed line ranges (the pre-edit code, which the synced index reflects), asks
codegraph "which symbol owns these lines?" (tightest enclosing node), and counts the reverse call-like
edges into that node. PEBRA owns the percentile math (core.score_math.fractional_rank); codegraph owns
identity + cross-file resolution.

Boundaries (Architecture "one rule"): this is an ADAPTER — it may use stdlib I/O (sqlite3 read-only,
subprocess for the freshness gate) but imports only ``pebra.core`` + ``pebra.ports``. It is fail-soft:
codegraph absent / DB missing / index stale -> ``FanInEvidence(resolution_method='unresolved')``
with a ``fallback_reason``; it never raises and never fabricates fan-in.

Verified codegraph facts (schema v5): nodes(id, kind, name, qualified_name, file_path, start_line,
end_line — 1-based, repo-relative POSIX paths); edges(source, target, kind, provenance). Call-like
fan-in = ``calls``/``references``/``instantiates`` (``imports`` is file/module-level and would inflate
per-symbol fan-in, so it is excluded). Freshness from ``codegraph status --json``: fresh iff
pendingChanges is empty AND index.reindexRecommended is false.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pebra.core.models import CandidateAction, FanInEvidence
from pebra.core.score_math import fractional_rank

# Edge kinds that constitute per-symbol fan-in. 'imports' is deliberately excluded (file/module-level).
_FANIN_EDGE_KINDS = ("calls", "references", "instantiates")
# The fan-in population (what gets a percentile rank). Mirrors codegraph's callable NodeKinds.
_CALLABLE_KINDS = ("function", "method", "class", "struct", "interface", "trait", "protocol")
# Location-resolution owner kinds — broader, so a change always maps to *some* owning scope.
_OWNER_KINDS = _CALLABLE_KINDS + ("component", "route", "namespace", "module")
_MIN_SCHEMA_VERSION = 5

_INSTALL_HINT = "install with: npm install -g @colbymchenry/codegraph (or run: pebra setup-graph)"
_INIT_HINT = "run: pebra setup-graph"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def parse_old_side_ranges(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Extract OLD-SIDE (pre-edit) *changed* line ranges per file from a unified diff.

    Precision matters: a hunk header's old count spans CONTEXT lines too, so ranking off the header
    would let a small edit near a boundary grab a neighbouring symbol. Instead we walk each hunk body
    and collect only the old-side lines actually removed/changed (``-`` lines, excluding the ``---``
    header). A pure-insertion hunk (no ``-`` lines) collapses to the point ``(old_start, old_start)``
    so the enclosing scope still resolves. Contiguous changed lines are merged into ``(lo, hi)`` ranges.

    Keyed by the old-side path with any ``a/`` prefix stripped (codegraph's repo-relative POSIX form).
    ``/dev/null`` (added/deleted file) is skipped. Returns {} on anything that isn't a diff.
    """
    per_file: dict[str, list[int]] = {}
    current: str | None = None
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current = None
            else:
                if raw.startswith("a/"):
                    raw = raw[2:]
                current = raw.replace("\\", "/")
            i += 1
            continue
        hm = _HUNK_RE.match(line)
        if hm and current is not None:
            old_start = int(hm.group(1))
            old_ln = old_start
            changed: list[int] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(("@@", "--- ", "+++ ", "diff ")):
                body = lines[i]
                if body.startswith("-"):  # old-side removed/changed line (never a '---' header here)
                    changed.append(old_ln)
                    old_ln += 1
                elif body.startswith("+"):  # added line — consumes no old-side line
                    pass
                else:  # context (' '), '\ No newline', or blank — advances the old-side cursor
                    old_ln += 1
                i += 1
            per_file.setdefault(current, []).extend(changed or [old_start])
            continue
        i += 1
    return {f: _merge_contiguous(ls) for f, ls in per_file.items()}


def _merge_contiguous(line_numbers: list[int]) -> list[tuple[int, int]]:
    """Collapse a list of line numbers into sorted, merged inclusive (lo, hi) ranges."""
    if not line_numbers:
        return []
    ordered = sorted(set(line_numbers))
    ranges: list[tuple[int, int]] = []
    lo = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append((lo, prev))
            lo = prev = n
    ranges.append((lo, prev))
    return ranges


def _default_status(repo_root: str) -> dict[str, Any] | None:
    """Real freshness gate — STATUS-FIRST, then a *conditional* repair sync.

    Ordering is load-bearing: ``codegraph sync`` must NEVER run before we know the index state, because
    a worktree mismatch means the resolved index belongs to a *different* worktree — syncing it would
    refresh (and mutate) the wrong, borrowed index without fixing the mismatch. So:

        status  ->  if absent/uninitialized/worktree-mismatch/already-fresh: stop (no sync)
                ->  else (initialized, same worktree, merely stale): sync to repair, then re-status

    Returns the parsed status dict, or None if the codegraph CLI is unavailable / errors / times out.
    The path is POSITIONAL on ``sync``/``status`` (no ``--path`` option on those two)."""
    if shutil.which("codegraph") is None:
        return None  # binary not on PATH -> don't even spawn (caller emits an install hint)
    try:
        initial = _run_status(repo_root)
        if initial is None:
            return None
        # Only an initialized, same-worktree, merely-stale index is safe to repair with sync.
        if (
            initial.get("initialized") is False
            or initial.get("worktreeMismatch")
            or _is_fresh(initial)
        ):
            return initial
        subprocess.run(
            ["codegraph", "sync", repo_root],
            capture_output=True, text=True, timeout=120, check=False,
        )
        post = _run_status(repo_root)
        return post if post is not None else initial
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _run_status(repo_root: str) -> dict[str, Any] | None:
    """One ``codegraph status <repo> --json`` probe -> parsed dict, or None on failure/bad JSON."""
    proc = subprocess.run(
        ["codegraph", "status", repo_root, "--json"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _is_fresh(status: dict[str, Any]) -> bool:
    if status.get("initialized") is False:
        return False
    if status.get("worktreeMismatch"):
        return False
    pending = status.get("pendingChanges") or {}
    has_pending = any(pending.get(k) for k in ("added", "modified", "removed"))
    reindex = bool((status.get("index") or {}).get("reindexRecommended"))
    return not has_pending and not reindex


def _db_path_from_status(repo_root: str, status: dict[str, Any]) -> Path:
    """Return the codegraph DB path from status.indexPath when available.

    CodeGraph supports CODEGRAPH_DIR / nearest-index resolution and reports the actual index
    directory in ``status --json``. Fall back to the default layout for fixture tests and older
    status payloads.
    """
    index_path = status.get("indexPath")
    if isinstance(index_path, str) and index_path.strip():
        return Path(index_path) / "codegraph.db"
    return Path(repo_root) / ".codegraph" / "codegraph.db"


class CodeGraphAdapter:
    """Reads codegraph's SQLite for per-symbol fan-in; shells to the CLI only for the freshness gate.

    ``status_fn`` is injectable so the SQL/parse/math are unit-testable without the binary; it defaults
    to the real ``codegraph sync`` + ``status --json`` subprocess path.
    """

    def __init__(self, status_fn: Callable[[str], dict[str, Any] | None] | None = None) -> None:
        self._status_fn = status_fn or _default_status
        self._dist_cache: dict[tuple[str, float], list[int]] = {}

    def fanin(self, action: CandidateAction, repo_root: str) -> FanInEvidence:
        status = self._status_fn(repo_root)
        if status is None:
            return _unresolved("unknown", f"codegraph CLI not found; {_INSTALL_HINT}")
        if status.get("initialized") is False:
            return _unresolved("unknown", f"codegraph index not initialized; {_INIT_HINT}")
        if not _is_fresh(status):
            if status.get("worktreeMismatch"):
                # The resolved index belongs to a DIFFERENT worktree (borrowed). The fix is a
                # worktree-local index (codegraph init -i), NOT a sync — surfaced via setup-graph --fix.
                return _unresolved(
                    "stale",
                    "codegraph worktree mismatch (index belongs to another worktree); "
                    "run: pebra setup-graph --fix",
                )
            return _unresolved(
                "stale", "codegraph index stale after sync; run: pebra doctor --fix-graph"
            )

        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return _unresolved("unknown", f"codegraph DB not found; {_INIT_HINT}")

        try:
            uri = db_path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return _unresolved("unknown", f"codegraph DB could not be opened: {exc}")
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return _unresolved("fresh", f"codegraph schema below v{_MIN_SCHEMA_VERSION}")
            cg_ver, ext_ver = self._versions(con)
            node_ids, method = self._resolve(con, action, repo_root)
            if not node_ids:
                return FanInEvidence(
                    resolution_method="unresolved", graph_freshness="fresh",
                    provider_version=cg_ver, index_version=ext_ver,
                    fallback_reason="changed symbol could not be located in the graph",
                )
            if method == "name_fallback_ambiguous":
                # Agent-supplied name matched >1 symbol and location resolution did not succeed:
                # carry the candidates for provenance but DO NOT emit trusted fan-in (zero, untrusted).
                return FanInEvidence(
                    resolution_method="name_fallback_ambiguous",
                    node_ids_resolved=tuple(node_ids),
                    graph_freshness="fresh",
                    provider_version=cg_ver, index_version=ext_ver,
                    fallback_reason="ambiguous name match; fan-in not trusted (no location resolution)",
                )
            count, pctl = self._fanin(con, node_ids, db_path)
            return FanInEvidence(
                symbol_fan_in_percentile=pctl,
                symbol_caller_count=count,
                resolution_method=method,
                node_ids_resolved=tuple(node_ids),
                provider_version=cg_ver,
                index_version=ext_ver,
                graph_freshness="fresh",
            )
        except (sqlite3.Error, OSError) as exc:
            # never let a corrupt/locked/half-written DB crash the assessment (fail-soft contract)
            return _unresolved("unknown", f"codegraph DB query failed: {exc}")
        finally:
            con.close()

    # --- internals ---

    @staticmethod
    def _schema_version(con: sqlite3.Connection) -> int:
        row = con.execute("SELECT MAX(version) AS v FROM schema_versions").fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    @staticmethod
    def _versions(con: sqlite3.Connection) -> tuple[str | None, str | None]:
        rows = con.execute(
            "SELECT key, value FROM project_metadata WHERE key IN "
            "('indexed_with_version', 'indexed_with_extraction_version')"
        ).fetchall()
        meta = {r["key"]: r["value"] for r in rows}
        return meta.get("indexed_with_version"), meta.get("indexed_with_extraction_version")

    def _resolve(
        self, con: sqlite3.Connection, action: CandidateAction, repo_root: str
    ) -> tuple[list[str], str]:
        """Location-first: resolve ALL changed symbols by (file, old-side line range), unioning the
        tightest owners across every changed range (a hunk spanning two functions resolves both).
        Falls back to name-matching ``affected_symbols`` only when there is no patch / no location."""
        ranges = parse_old_side_ranges(action.proposed_patch or "")
        node_ids: list[str] = []
        for raw_path, spans in ranges.items():
            rel = _repo_relative(raw_path, repo_root)
            for lo, hi in spans:
                for nid in self._owners_at(con, rel, lo, hi):
                    if nid not in node_ids:
                        node_ids.append(nid)
        if node_ids:
            return node_ids, "location"
        return self._name_fallback(con, action, repo_root)

    @staticmethod
    def _owners_at(con: sqlite3.Connection, file_path: str, lo: int, hi: int) -> list[str]:
        """All owner nodes overlapping [lo, hi], reduced to the tightest leaves: a node is dropped
        when it strictly contains another overlapping node (so a class is dropped in favour of its
        changed method, but two changed sibling functions are both kept)."""
        placeholders = ",".join("?" * len(_OWNER_KINDS))
        rows = con.execute(
            f"SELECT id, start_line, end_line FROM nodes WHERE file_path = ? "
            f"AND start_line <= ? AND end_line >= ? AND kind IN ({placeholders}) "
            f"ORDER BY (end_line - start_line) ASC",
            (file_path, hi, lo, *_OWNER_KINDS),
        ).fetchall()
        kept: list[str] = []
        for r in rows:
            span = r["end_line"] - r["start_line"]
            contains_child = any(
                other["id"] != r["id"]
                and r["start_line"] <= other["start_line"]
                and r["end_line"] >= other["end_line"]
                and span > (other["end_line"] - other["start_line"])
                for other in rows
            )
            if not contains_child:
                kept.append(r["id"])
        return kept

    @staticmethod
    def _resolve_named(
        con: sqlite3.Connection, symbol_id: str, repo_root: str
    ) -> tuple[list[str], bool]:
        """Resolve ONE 'file::Qualified' symbol_id to node ids by name. Returns (ids, ambiguous).

        Separator-tolerant: the assess path supplies '::'-qualified names while the verify path's AST
        diff supplies '.'-qualified names (e.g. ``LoginManager.validate_login``); codegraph stores '::'
        (``LoginManager::validate_login``). We match the qualified_name in EITHER separator first
        (precise — a class method resolves to exactly its node), and only fall back to the leaf ``name``
        when no qualified match exists, so a qualified id never over-matches an unrelated same-leaf symbol.
        """
        file_part, _, qual = symbol_id.partition("::")
        if not qual:
            return [], False
        rel = _repo_relative(file_part, repo_root)
        qual_cg = qual.replace(".", "::")  # AST '.' separator -> codegraph '::'
        rows = con.execute(
            "SELECT id FROM nodes WHERE file_path = ? AND qualified_name IN (?, ?)",
            (rel, qual, qual_cg),
        ).fetchall()
        if not rows:  # fall back to the leaf name only when the qualified name didn't resolve
            leaf = qual.replace("::", ".").split(".")[-1]
            rows = con.execute(
                "SELECT id FROM nodes WHERE file_path = ? AND name = ?", (rel, leaf)
            ).fetchall()
        ids = [r["id"] for r in rows]
        return ids, len(ids) > 1

    @staticmethod
    def _name_fallback(
        con: sqlite3.Connection, action: CandidateAction, repo_root: str
    ) -> tuple[list[str], str]:
        resolved: list[str] = []
        ambiguous = False
        for sym in action.affected_symbols:
            ids, amb = CodeGraphAdapter._resolve_named(con, sym, repo_root)
            if amb:
                ambiguous = True
            for nid in ids:
                if nid not in resolved:
                    resolved.append(nid)
        if not resolved:
            return [], "unresolved"
        return resolved, "name_fallback_ambiguous" if ambiguous else "name_fallback"

    def percentiles_by_name(self, symbol_ids: list[str], repo_root: str) -> dict[str, float]:
        """Per-symbol TRUSTED fan-in percentile keyed by symbol_id ('file::Qualified').

        Used by the post-edit verify path to fill ``callers_percentile`` symmetrically with the assess
        path. A symbol is omitted from the result (caller reads 0.0) when the graph is absent / stale /
        worktree-mismatched / its name is ambiguous or unresolvable — i.e. never a fabricated fan-in.
        Runs the freshness gate + opens the DB once for the whole batch."""
        if not symbol_ids:
            return {}
        status = self._status_fn(repo_root)
        if (
            status is None
            or status.get("initialized") is False
            or status.get("worktreeMismatch")
            or not _is_fresh(status)
        ):
            return {}
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return {}
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError):
            return {}
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return {}
            out: dict[str, float] = {}
            for sid in symbol_ids:
                node_ids, ambiguous = self._resolve_named(con, sid, repo_root)
                if node_ids and not ambiguous:  # ambiguous = untrusted -> omit (0.0)
                    _, pctl = self._fanin(con, node_ids, db_path)
                    out[sid] = pctl
            return out
        except (sqlite3.Error, OSError):
            return {}
        finally:
            con.close()

    def _fanin(
        self, con: sqlite3.Connection, node_ids: list[str], db_path: Path
    ) -> tuple[int, float]:
        """Union fan-in across the resolved symbol(s): the count of DISTINCT caller sources whose
        call-like edges target any resolved node (a source that calls a target many times counts once),
        ranked against the repo-wide per-callable distinct-caller distribution."""
        edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
        id_ph = ",".join("?" * len(node_ids))
        row = con.execute(
            f"SELECT COUNT(DISTINCT source) AS c FROM edges "
            f"WHERE target IN ({id_ph}) AND kind IN ({edge_ph})",
            (*node_ids, *_FANIN_EDGE_KINDS),
        ).fetchone()
        caller_count = int(row["c"])
        distribution = self._distribution(con, db_path)
        return caller_count, fractional_rank(caller_count, distribution)

    def _distribution(self, con: sqlite3.Connection, db_path: Path) -> list[int]:
        key = (str(db_path), os.path.getmtime(db_path))
        cached = self._dist_cache.get(key)
        if cached is not None:
            return cached
        edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
        call_ph = ",".join("?" * len(_CALLABLE_KINDS))
        nonzero = [
            int(r["c"]) for r in con.execute(
                f"SELECT COUNT(DISTINCT e.source) AS c FROM edges e JOIN nodes n ON n.id = e.target "
                f"WHERE e.kind IN ({edge_ph}) AND n.kind IN ({call_ph}) GROUP BY e.target",
                (*_FANIN_EDGE_KINDS, *_CALLABLE_KINDS),
            ).fetchall()
        ]
        total = con.execute(
            f"SELECT COUNT(*) AS c FROM nodes WHERE kind IN ({call_ph})", _CALLABLE_KINDS
        ).fetchone()["c"]
        zeros = max(0, int(total) - len(nonzero))
        distribution = sorted([0] * zeros + nonzero)
        self._dist_cache[key] = distribution
        return distribution


def _repo_relative(path: str, repo_root: str) -> str:
    """Normalize a path to codegraph's stored form: repo-relative, forward-slash."""
    p = path.replace("\\", "/").strip()
    rr = repo_root.replace("\\", "/").rstrip("/")
    if rr and p.lower().startswith(rr.lower() + "/"):
        p = p[len(rr) + 1:]
    if p.startswith("./"):
        p = p[2:]
    return p


def _unresolved(freshness: str, reason: str) -> FanInEvidence:
    return FanInEvidence(
        resolution_method="unresolved", graph_freshness=freshness, fallback_reason=reason
    )
