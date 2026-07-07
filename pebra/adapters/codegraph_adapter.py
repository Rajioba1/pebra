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

import difflib
import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine
from pebra.core.graph_version import CODEGRAPH_ACCEPTED_RANGE, in_accepted_range
from pebra.core.language_capability import LanguageCapability
from pebra.core.models import CandidateAction, FanInEvidence, FileFanInRollup
from pebra.core.score_math import fractional_rank

# Edge kinds that constitute per-symbol fan-in. 'imports' is deliberately excluded (file/module-level).
_FANIN_EDGE_KINDS = ("calls", "references", "instantiates")
# Edge kinds that can be impacted by a MODIFY to a contract-bearing symbol. This is a deduped union
# signal, not an additive bonus: a node that both calls and implements the target counts once.
_MODIFY_IMPACT_EDGE_KINDS = _FANIN_EDGE_KINDS + ("implements", "extends")
# The fan-in population (what gets a percentile rank). Mirrors codegraph's callable NodeKinds.
_CALLABLE_KINDS = ("function", "method", "class", "struct", "interface", "trait", "protocol")
# Location-resolution owner kinds — broader, so a change always maps to *some* owning scope.
_OWNER_KINDS = _CALLABLE_KINDS + ("component", "route", "namespace", "module")
_CONTRACT_CONTAINER_KINDS = ("class", "struct", "interface", "trait", "protocol")
_CONTAINER_HIERARCHY_KINDS = _CONTRACT_CONTAINER_KINDS + ("namespace", "module", "file", "component", "route")
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


def _changed_after_side_ranges(before: str, after: str) -> list[tuple[int, int]]:
    """AFTER-side (1-based, inclusive) line ranges that differ between ``before`` and ``after``.

    Verify runs POST-edit, so the graph indexes the CURRENT (after) worktree; owners must be resolved
    against after-side line numbers (unlike the assess path, which uses the un-applied patch's OLD
    side). A pure deletion is mapped to the surviving boundary line so its enclosing owner is still
    picked up."""
    a, b = before.splitlines(), after.splitlines()
    lines: list[int] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            lines.append(max(1, j1))  # after-side boundary where the deletion landed
        else:  # replace / insert -> the new/changed after-side lines
            lines.extend(range(j1 + 1, j2 + 1))
    return _merge_contiguous(lines)


def _default_status(repo_root: str) -> dict[str, Any] | None:
    """Real freshness gate — STATUS-FIRST, then a *conditional* repair sync.

    Ordering is load-bearing: ``codegraph sync`` must NEVER run before we know the index state, because
    a worktree mismatch means the resolved index belongs to a *different* worktree — syncing it would
    refresh (and mutate) the wrong, borrowed index without fixing the mismatch. So:

        status  ->  if absent/uninitialized/worktree-mismatch/already-fresh: stop (no sync)
                ->  else (initialized, same worktree, merely stale): sync to repair, then re-status

    Returns the parsed status dict, or None if the codegraph CLI is unavailable / errors / times out.
    The path is POSITIONAL on ``sync``/``status`` (no ``--path`` option on those two)."""
    exe = find_engine()  # PEBRA_CODEGRAPH_BIN -> PATH -> managed install (works in a fresh shell)
    if exe is None:
        return None  # engine not found anywhere -> don't even spawn (caller emits an install hint)
    try:
        initial = _run_status(repo_root, exe)
        if initial is None:
            return None
        # Only an initialized, same-worktree, merely-stale index is safe to repair with sync.
        if (
            initial.get("initialized") is False
            or initial.get("worktreeMismatch")
            or _is_fresh(initial)
        ):
            return initial
        # resolve_engine_argv handles the Windows .cmd shim (codegraph has no .exe) — a bare
        # ["codegraph", ...] FileNotFoundErrors on Windows even when installed.
        subprocess.run(
            resolve_engine_argv(exe, ["sync", repo_root]),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )
        post = _run_status(repo_root, exe)
        return post if post is not None else initial
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _run_status(repo_root: str, exe: str) -> dict[str, Any] | None:
    """One ``codegraph status <repo> --json`` probe -> parsed dict, or None on failure/bad JSON.
    ``exe`` is the resolved launcher path (from find_engine) so the Windows .cmd shim is invoked
    correctly — callers must pre-resolve (no bare-name default, which would FileNotFound on Windows)."""
    proc = subprocess.run(
        resolve_engine_argv(exe, ["status", repo_root, "--json"]),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
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
        self._impact_dist_cache: dict[tuple[str, float], list[int]] = {}
        # Memoize the capability probe per repo_root for this adapter's lifetime (one assess()/CLI call):
        # fanin() already spawned a `codegraph status` subprocess for the same repo, so re-probing per
        # action would double the subprocess count. Same-lifetime staleness is a non-issue (the adapter
        # is rebuilt per assess() in composition), mirroring _dist_cache's per-instance scope.
        self._probe_cache: dict[str, tuple[dict[str, LanguageCapability], bool, str | None]] = {}

    def node_counts(self, repo_root: str) -> dict[str, int]:
        """Repo-wide CodeGraph node counts for an INDEPENDENT graph-validity check (used by the A/B
        graph preflight to catch a 'fresh' index that actually picked up no nodes). Returns
        ``{"total","callable","csharp_callable"}`` — all 0 when the graph is absent / uninitialized /
        unreadable / below the min schema. Honest zeros, never fabricated. Read-only; never mutates."""
        zero = {"total": 0, "callable": 0, "csharp_callable": 0}
        status = self._status_fn(repo_root)
        if status is None or status.get("initialized") is False:
            return zero
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return zero
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError):
            return zero
        con.row_factory = sqlite3.Row  # _schema_version reads row["v"]
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return zero
            ph = ",".join("?" * len(_CALLABLE_KINDS))
            total = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            callable_ = con.execute(
                f"SELECT COUNT(*) FROM nodes WHERE kind IN ({ph})", _CALLABLE_KINDS
            ).fetchone()[0]
            csharp = con.execute(
                f"SELECT COUNT(*) FROM nodes WHERE kind IN ({ph}) AND lower(file_path) LIKE '%.cs'",
                _CALLABLE_KINDS,
            ).fetchone()[0]
            return {"total": int(total), "callable": int(callable_), "csharp_callable": int(csharp)}
        except sqlite3.Error:
            return zero
        finally:
            con.close()

    def probe_capabilities(self, repo_root: str) -> dict[str, LanguageCapability]:
        """MEASURED per-language capability from the indexed graph: for each language, the callable-node
        count and the fraction of those nodes carrying a signature / a visibility, plus the edge kinds
        it sources. Structural (coverage doesn't change with staleness) so it mirrors ``node_counts``'
        gates — initialized + schema + readable DB — NOT the freshness gate. Fail-soft: returns ``{}``
        when the graph is absent/uninitialized/unreadable/below-schema. Never raises."""
        caps, _ok, _reason = self._probe(repo_root)
        return caps

    def capability_for(self, language: str, repo_root: str) -> LanguageCapability:
        """The measured capability for one language: ``graph_unavailable`` when the probe couldn't read
        the graph at all; ``measured`` with ``node_count=0`` when the graph is readable but has no
        callable nodes for that language (honest 'indexed but nothing to classify')."""
        caps, ok, reason = self._probe(repo_root)
        if not ok:
            return LanguageCapability(
                language=language, probe_status="graph_unavailable", fallback_reason=reason
            )
        return caps.get(
            language,
            LanguageCapability(language=language, probe_status="measured", node_count=0),
        )

    def _probe(self, repo_root: str) -> tuple[dict[str, LanguageCapability], bool, str | None]:
        if repo_root in self._probe_cache:
            return self._probe_cache[repo_root]
        result = self._probe_uncached(repo_root)
        self._probe_cache[repo_root] = result
        return result

    def _probe_uncached(
        self, repo_root: str
    ) -> tuple[dict[str, LanguageCapability], bool, str | None]:
        status = self._status_fn(repo_root)
        if status is None:
            return {}, False, "codegraph CLI not found"
        # Capability is a TRUST claim ("this language is measured as supported"), so — unlike the plain
        # node_counts preflight — it must reject the same untrusted-index states fanin() rejects: an
        # out-of-range codegraph version (different extraction semantics) and a borrowed/foreign
        # worktree index (a DIFFERENT codebase). Ordinary staleness is NOT gated: coverage (does a
        # language carry signatures/visibility) is structural and doesn't drift with pending changes.
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return {}, False, f"codegraph version {runtime_ver} outside accepted range"
        if status.get("worktreeMismatch"):
            return {}, False, "codegraph index belongs to another worktree"
        if status.get("initialized") is False:
            return {}, False, "codegraph index not initialized"
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return {}, False, "codegraph DB not found"
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return {}, False, f"codegraph DB could not be opened: {exc}"
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return {}, False, f"codegraph schema below v{_MIN_SCHEMA_VERSION}"
            ph = ",".join("?" * len(_CALLABLE_KINDS))
            rows = con.execute(
                f"SELECT language AS lang, COUNT(*) AS n, "
                f"SUM(CASE WHEN signature IS NOT NULL AND signature <> '' THEN 1 ELSE 0 END) AS sig_n, "
                f"SUM(CASE WHEN visibility IS NOT NULL AND visibility <> '' THEN 1 ELSE 0 END) AS vis_n "
                f"FROM nodes WHERE kind IN ({ph}) GROUP BY language",
                _CALLABLE_KINDS,
            ).fetchall()
            edges_by_lang: dict[str, set[str]] = {}
            for er in con.execute(
                f"SELECT src.language AS lang, e.kind AS kind FROM edges e "
                f"JOIN nodes src ON src.id = e.source WHERE src.kind IN ({ph}) "
                f"GROUP BY src.language, e.kind",
                _CALLABLE_KINDS,
            ).fetchall():
                if er["lang"] and er["kind"]:
                    edges_by_lang.setdefault(str(er["lang"]), set()).add(str(er["kind"]))
            caps: dict[str, LanguageCapability] = {}
            for r in rows:
                lang = r["lang"]
                if not lang:
                    continue
                n = int(r["n"])
                sig = int(r["sig_n"] or 0)
                vis = int(r["vis_n"] or 0)
                caps[str(lang)] = LanguageCapability(
                    language=str(lang), probe_status="measured", node_count=n,
                    signature_coverage_ratio=(sig / n) if n else 0.0,
                    visibility_coverage_ratio=(vis / n) if n else 0.0,
                    edge_kinds=frozenset(edges_by_lang.get(str(lang), set())),
                )
            return caps, True, None
        except (sqlite3.Error, OSError) as exc:
            return {}, False, f"codegraph DB query failed: {exc}"
        finally:
            con.close()

    def dependent_files_result(self, file_path: str, repo_root: str) -> dict[str, Any]:
        """Structured file-level blast-radius result.

        ``available=False`` means the graph could not be trusted/read, which is distinct from
        ``available=True`` with an empty ``dependent_files`` list (a real zero-dependent result).
        """
        status = self._status_fn(repo_root)
        if status is None:
            return _dependents_unavailable("unknown", "codegraph CLI not found")
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return _dependents_unavailable(
                "unknown", f"codegraph version {runtime_ver} out of range"
            )
        if status.get("initialized") is False:
            return _dependents_unavailable("unknown", "codegraph index not initialized")
        if not _is_fresh(status):
            return _dependents_unavailable("stale", "codegraph index stale or worktree-mismatched")
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return _dependents_unavailable("unknown", "codegraph DB not found")
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError):
            return _dependents_unavailable("unknown", "codegraph DB could not be opened")
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return _dependents_unavailable(
                    "fresh", f"codegraph schema below v{_MIN_SCHEMA_VERSION}"
                )
            rel = _repo_relative(file_path, repo_root)
            call_ph = ",".join("?" * len(_CALLABLE_KINDS))
            edge_ph = ",".join("?" * len(_MODIFY_IMPACT_EDGE_KINDS))
            rows = con.execute(
                f"SELECT DISTINCT src.file_path AS f FROM edges e "
                f"JOIN nodes tgt ON tgt.id = e.target JOIN nodes src ON src.id = e.source "
                f"WHERE tgt.file_path = ? AND tgt.kind IN ({call_ph}) AND e.kind IN ({edge_ph}) "
                f"AND src.file_path IS NOT NULL AND src.file_path != ? ORDER BY src.file_path",
                (rel, *_CALLABLE_KINDS, *_MODIFY_IMPACT_EDGE_KINDS, rel),
            ).fetchall()
            files = [r["f"] for r in rows if r["f"]]
            return {
                "available": True,
                "graph_freshness": "fresh",
                "dependent_files": files,
                "count": len(files),
                "fallback_reason": None,
            }
        except sqlite3.Error:
            return _dependents_unavailable("unknown", "codegraph DB query failed")
        finally:
            con.close()

    def dependent_files(self, file_path: str, repo_root: str) -> list[str]:
        """Compatibility wrapper returning only dependent paths.

        Prefer ``dependent_files_result`` for agent-facing guidance so graph-unavailable and real-zero
        cases remain distinguishable.
        """
        result = self.dependent_files_result(file_path, repo_root)
        files = result.get("dependent_files", [])
        return list(files) if isinstance(files, list) else []

    def fanin(self, action: CandidateAction, repo_root: str) -> FanInEvidence:
        status = self._status_fn(repo_root)
        if status is None:
            return _unresolved("unknown", f"codegraph CLI not found; {_INSTALL_HINT}")
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            # running an unsupported codegraph version -> untrusted (its fan-in/extraction semantics may
            # differ from what PEBRA validated). Gate 13 routes this to inspect_first under require_graph.
            return _unresolved(
                "unknown",
                f"codegraph version {runtime_ver} is outside the accepted range "
                f"{CODEGRAPH_ACCEPTED_RANGE}; run: pebra setup-graph --fix",
            )
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
            ctx = self._graph_context(con, node_ids, self._impact_distribution(con, db_path))
            return FanInEvidence(
                symbol_fan_in_percentile=pctl,
                symbol_caller_count=count,
                resolution_method=method,
                node_ids_resolved=tuple(node_ids),
                provider_version=cg_ver,
                index_version=ext_ver,
                graph_freshness="fresh",
                owner_kinds=ctx["owner_kinds"],
                max_owner_span_lines=ctx["max_owner_span_lines"],
                resolved_symbol_count=ctx["resolved_symbol_count"],
                incoming_edge_counts=ctx["incoming_edge_counts"],
                outgoing_edge_counts=ctx["outgoing_edge_counts"],
                modify_impact_count=ctx["modify_impact_count"],
                modify_impact_percentile=ctx["modify_impact_percentile"],
                modify_impact_edge_counts=ctx["modify_impact_edge_counts"],
                container_hierarchy_kinds=ctx["container_hierarchy_kinds"],
                graph_file_size_bytes=ctx["graph_file_size_bytes"],
                graph_file_node_count=ctx["graph_file_node_count"],
                graph_file_error_count=ctx["graph_file_error_count"],
                contract_surface_kind=ctx["contract_surface_kind"],
                is_exported_contract=ctx["is_exported_contract"],
                is_abstract_or_interface_contract=ctx["is_abstract_or_interface_contract"],
                has_signature_metadata=ctx["has_signature_metadata"],
                resolved_language=ctx["resolved_language"],
                resolved_qualified_names=ctx["resolved_qualified_names"],
            )
        except (sqlite3.Error, OSError) as exc:
            # never let a corrupt/locked/half-written DB crash the assessment (fail-soft contract)
            return _unresolved("unknown", f"codegraph DB query failed: {exc}")
        finally:
            con.close()

    def structural_symbols(
        self, file_path: str, before: str | None, after: str | None, repo_root: str
    ) -> FanInEvidence:
        """Post-edit sibling of ``fanin()`` for the multi-language verify tier: resolve the owners of an
        already-applied change from full before/after text (no patch string) and return the same
        ``FanInEvidence``. The caller turns it into coarse classifier rows via
        ``change_classifier.rows_from_fanin`` — the reason non-Python files no longer fall through the
        verifier's ``.py``-only reclassification. Mirrors ``fanin()``'s freshness/version/schema/DB
        fail-soft gates exactly; never raises."""
        status = self._status_fn(repo_root)
        if status is None:
            return _unresolved("unknown", f"codegraph CLI not found; {_INSTALL_HINT}")
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return _unresolved("unknown", f"codegraph version {runtime_ver} outside accepted range")
        if status.get("initialized") is False:
            return _unresolved("unknown", f"codegraph index not initialized; {_INIT_HINT}")
        if not _is_fresh(status):
            return _unresolved("stale", "codegraph index stale; run: pebra doctor --fix-graph")
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return _unresolved("unknown", f"codegraph DB not found; {_INIT_HINT}")
        if after is None:
            return _unresolved(
                "fresh", "file has no post-edit content (deleted); no current-graph owners"
            )
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return _unresolved("unknown", f"codegraph DB could not be opened: {exc}")
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return _unresolved("fresh", f"codegraph schema below v{_MIN_SCHEMA_VERSION}")
            cg_ver, ext_ver = self._versions(con)
            rel = _repo_relative(file_path, repo_root)
            node_ids: list[str] = []
            for lo, hi in _changed_after_side_ranges(before or "", after):
                for nid in self._owners_at(con, rel, lo, hi):
                    if nid not in node_ids:
                        node_ids.append(nid)
            if not node_ids:
                return FanInEvidence(
                    resolution_method="unresolved", graph_freshness="fresh",
                    provider_version=cg_ver, index_version=ext_ver,
                    fallback_reason="changed lines did not resolve to a graph owner",
                )
            count, pctl = self._fanin(con, node_ids, db_path)
            ctx = self._graph_context(con, node_ids, self._impact_distribution(con, db_path))
            return FanInEvidence(
                symbol_fan_in_percentile=pctl, symbol_caller_count=count,
                resolution_method="location", node_ids_resolved=tuple(node_ids),
                provider_version=cg_ver, index_version=ext_ver, graph_freshness="fresh",
                owner_kinds=ctx["owner_kinds"], max_owner_span_lines=ctx["max_owner_span_lines"],
                resolved_symbol_count=ctx["resolved_symbol_count"],
                incoming_edge_counts=ctx["incoming_edge_counts"],
                outgoing_edge_counts=ctx["outgoing_edge_counts"],
                modify_impact_count=ctx["modify_impact_count"],
                modify_impact_percentile=ctx["modify_impact_percentile"],
                modify_impact_edge_counts=ctx["modify_impact_edge_counts"],
                container_hierarchy_kinds=ctx["container_hierarchy_kinds"],
                graph_file_size_bytes=ctx["graph_file_size_bytes"],
                graph_file_node_count=ctx["graph_file_node_count"],
                graph_file_error_count=ctx["graph_file_error_count"],
                contract_surface_kind=ctx["contract_surface_kind"],
                is_exported_contract=ctx["is_exported_contract"],
                is_abstract_or_interface_contract=ctx["is_abstract_or_interface_contract"],
                has_signature_metadata=ctx["has_signature_metadata"],
                resolved_language=ctx["resolved_language"],
                resolved_qualified_names=ctx["resolved_qualified_names"],
            )
        except (sqlite3.Error, OSError) as exc:
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
            or (status.get("version") and not in_accepted_range(status["version"]))
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

    def file_fanin_rollup(self, file_path: str, repo_root: str) -> FileFanInRollup:
        """Aggregate call-graph fan-in across ALL callable symbols in a file (whole-file destructive
        ops). Mirrors ``fanin()``'s freshness/version/DB gates; any gate failure or query error returns
        an ``unresolved`` rollup (fail-soft — never crashes the assessment)."""
        status = self._status_fn(repo_root)
        if status is None:
            return FileFanInRollup(fallback_reason="codegraph CLI not found")
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return FileFanInRollup(fallback_reason=f"codegraph version {runtime_ver} out of range")
        if status.get("initialized") is False:
            return FileFanInRollup(fallback_reason="codegraph index not initialized")
        if not _is_fresh(status):
            return FileFanInRollup(graph_freshness="stale", fallback_reason="codegraph index stale")
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return FileFanInRollup(fallback_reason="codegraph DB not found")
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return FileFanInRollup(fallback_reason=f"codegraph DB could not be opened: {exc}")
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return FileFanInRollup(
                    graph_freshness="fresh", fallback_reason=f"schema below v{_MIN_SCHEMA_VERSION}"
                )
            rel = _repo_relative(file_path, repo_root)
            edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
            call_ph = ",".join("?" * len(_CALLABLE_KINDS))
            distinct = int(con.execute(
                f"SELECT COUNT(DISTINCT e.source) AS c FROM edges e JOIN nodes n ON n.id = e.target "
                f"WHERE n.file_path = ? AND n.kind IN ({call_ph}) AND e.kind IN ({edge_ph})",
                (rel, *_CALLABLE_KINDS, *_FANIN_EDGE_KINDS),
            ).fetchone()["c"])
            mx = int(con.execute(
                f"SELECT COALESCE(MAX(cnt), 0) AS mx FROM ("
                f"SELECT COUNT(DISTINCT e.source) AS cnt FROM edges e JOIN nodes n ON n.id = e.target "
                f"WHERE n.file_path = ? AND n.kind IN ({call_ph}) AND e.kind IN ({edge_ph}) "
                f"GROUP BY e.target)",
                (rel, *_CALLABLE_KINDS, *_FANIN_EDGE_KINDS),
            ).fetchone()["mx"])
            sym_count = int(con.execute(
                f"SELECT COUNT(*) AS c FROM nodes WHERE file_path = ? AND kind IN ({call_ph})",
                (rel, *_CALLABLE_KINDS),
            ).fetchone()["c"])
            pctl = fractional_rank(distinct, self._distribution(con, db_path))
            return FileFanInRollup(
                max_caller_count=mx, distinct_caller_count=distinct, symbol_count=sym_count,
                file_symbol_fanin_rollup_percentile=pctl, resolution_method="file_location",
                graph_freshness="fresh",
            )
        except (sqlite3.Error, OSError) as exc:
            return FileFanInRollup(fallback_reason=f"codegraph DB query failed: {exc}")
        finally:
            con.close()

    def highest_file_fanin_percentile(self, file_path: str, repo_root: str) -> float | None:
        """Per-symbol MAX fan-in percentile for a file: fractional_rank(mx, repo distribution) where
        ``mx`` is the largest per-symbol distinct-caller count in the file. This is DISTINCT from
        ``file_fanin_rollup``'s ``file_symbol_fanin_rollup_percentile`` (whole-file aggregate); it asks
        "is the hottest symbol in this file high fan-in?" Returns None when the graph is
        absent/stale/uninitialized/below-schema (the caller treats None as 'no evidence' -> fail open).
        """
        status = self._status_fn(repo_root)
        if status is None:
            return None
        runtime_ver = status.get("version")
        if runtime_ver and not in_accepted_range(runtime_ver):
            return None
        if status.get("initialized") is False or not _is_fresh(status):
            return None
        db_path = _db_path_from_status(repo_root, status)
        if not db_path.is_file():
            return None
        try:
            con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        except (sqlite3.Error, OSError, ValueError):
            return None
        con.row_factory = sqlite3.Row
        try:
            if self._schema_version(con) < _MIN_SCHEMA_VERSION:
                return None
            rel = _repo_relative(file_path, repo_root)
            edge_ph = ",".join("?" * len(_FANIN_EDGE_KINDS))
            call_ph = ",".join("?" * len(_CALLABLE_KINDS))
            mx = int(con.execute(
                f"SELECT COALESCE(MAX(cnt), 0) AS mx FROM ("
                f"SELECT COUNT(DISTINCT e.source) AS cnt FROM edges e JOIN nodes n ON n.id = e.target "
                f"WHERE n.file_path = ? AND n.kind IN ({call_ph}) AND e.kind IN ({edge_ph}) "
                f"GROUP BY e.target)",
                (rel, *_CALLABLE_KINDS, *_FANIN_EDGE_KINDS),
            ).fetchone()["mx"])
            if mx <= 0:
                return None
            return fractional_rank(mx, self._distribution(con, db_path))
        except (sqlite3.Error, OSError):
            return None
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

    @staticmethod
    def _graph_context(
        con: sqlite3.Connection, node_ids: list[str], impact_distribution: list[int] | None = None
    ) -> dict[str, Any]:
        """Raw graph facts around the resolved owner node(s).

        Edge counts are distinct neighbouring nodes per edge kind, not raw edge rows. This keeps the
        signal stable if codegraph records multiple references between the same two symbols.
        """
        if not node_ids:
            return {
                "owner_kinds": (),
                "max_owner_span_lines": 0,
                "resolved_symbol_count": 0,
                "incoming_edge_counts": {},
                "outgoing_edge_counts": {},
                "modify_impact_count": 0,
                "modify_impact_percentile": 0.0,
                "modify_impact_edge_counts": {},
                "container_hierarchy_kinds": (),
                "graph_file_size_bytes": 0,
                "graph_file_node_count": 0,
                "graph_file_error_count": 0,
                "contract_surface_kind": "unknown",
                "is_exported_contract": False,
                "is_abstract_or_interface_contract": False,
                "has_signature_metadata": False,
                "resolved_language": None,
                "resolved_languages": (),
                "resolved_qualified_names": (),
            }
        id_ph = ",".join("?" * len(node_ids))
        rows = con.execute(
            f"SELECT id, kind, file_path, start_line, end_line, signature, return_type, type_parameters, "
            f"visibility, is_exported, is_abstract, language, qualified_name "
            f"FROM nodes WHERE id IN ({id_ph})",
            tuple(node_ids),
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        kinds: list[str] = []
        max_span = 0
        languages: list[str] = []
        qualified_names: list[str] = []
        for nid in node_ids:
            r = by_id.get(nid)
            if r is None:
                continue
            kind = r["kind"]
            if kind and kind not in kinds:
                kinds.append(kind)
            if r["start_line"] is not None and r["end_line"] is not None:
                max_span = max(max_span, int(r["end_line"]) - int(r["start_line"]) + 1)
            lang = r["language"]
            if lang and lang not in languages:
                languages.append(str(lang))
            qn = r["qualified_name"]
            if qn:
                qualified_names.append(str(qn))
        incoming = {
            str(r["kind"]): int(r["c"]) for r in con.execute(
                f"SELECT kind, COUNT(DISTINCT source) AS c FROM edges "
                f"WHERE target IN ({id_ph}) GROUP BY kind",
                tuple(node_ids),
            ).fetchall()
        }
        outgoing = {
            str(r["kind"]): int(r["c"]) for r in con.execute(
                f"SELECT kind, COUNT(DISTINCT target) AS c FROM edges "
                f"WHERE source IN ({id_ph}) GROUP BY kind",
                tuple(node_ids),
            ).fetchall()
        }
        impact_targets = CodeGraphAdapter._modify_impact_target_ids(con, node_ids)
        container_rows = CodeGraphAdapter._container_hierarchy_rows(con, node_ids)
        contract_containers = CodeGraphAdapter._contract_container_rows(con, node_ids)
        contract_meta = CodeGraphAdapter._contract_metadata(rows, contract_containers)
        file_meta = CodeGraphAdapter._file_metadata(con, rows)
        impact_id_ph = ",".join("?" * len(impact_targets))
        impact_ph = ",".join("?" * len(_MODIFY_IMPACT_EDGE_KINDS))
        impact_count = int(con.execute(
            f"SELECT COUNT(DISTINCT source) AS c FROM edges "
            f"WHERE target IN ({impact_id_ph}) AND kind IN ({impact_ph})",
            (*impact_targets, *_MODIFY_IMPACT_EDGE_KINDS),
        ).fetchone()["c"])
        impact_edges = {
            str(r["kind"]): int(r["c"]) for r in con.execute(
                f"SELECT kind, COUNT(DISTINCT source) AS c FROM edges "
                f"WHERE target IN ({impact_id_ph}) AND kind IN ({impact_ph}) GROUP BY kind",
                (*impact_targets, *_MODIFY_IMPACT_EDGE_KINDS),
            ).fetchall()
        }
        return {
            "owner_kinds": tuple(kinds),
            "max_owner_span_lines": max_span,
            "resolved_symbol_count": len(node_ids),
            "incoming_edge_counts": incoming,
            "outgoing_edge_counts": outgoing,
            "modify_impact_count": impact_count,
            "modify_impact_percentile": (
                fractional_rank(impact_count, impact_distribution or [0]) if impact_count else 0.0
            ),
            "modify_impact_edge_counts": impact_edges,
            "container_hierarchy_kinds": tuple(
                sorted({str(r["kind"]) for r in container_rows if r["kind"]})
            ),
            # The single-language fast path feeds the capability probe. Mixed-language patches are
            # reported explicitly so callers do not pretend the first language represents the whole edit.
            "resolved_language": languages[0] if languages else None,
            "resolved_languages": tuple(languages),
            "resolved_qualified_names": tuple(qualified_names),
            **file_meta,
            **contract_meta,
        }

    @staticmethod
    def _container_hierarchy_rows(
        con: sqlite3.Connection, node_ids: list[str]
    ) -> list[sqlite3.Row]:
        if not node_ids:
            return []
        id_ph = ",".join("?" * len(node_ids))
        container_ph = ",".join("?" * len(_CONTAINER_HIERARCHY_KINDS))
        return con.execute(
            f"WITH RECURSIVE ancestors(id, depth) AS ("
            f"  SELECT e.source, 1 FROM edges e JOIN nodes n ON n.id = e.source "
            f"  WHERE e.kind = 'contains' AND e.target IN ({id_ph}) "
            f"  AND n.kind IN ({container_ph}) "
            f"  UNION "
            f"  SELECT e.source, ancestors.depth + 1 FROM ancestors "
            f"  JOIN edges e ON e.target = ancestors.id "
            f"  JOIN nodes n ON n.id = e.source "
            f"  WHERE e.kind = 'contains' AND ancestors.depth < 8 "
            f"  AND n.kind IN ({container_ph})"
            f") "
            f"SELECT DISTINCT n.id, n.kind, n.start_line, n.end_line, n.signature, n.return_type, "
            f"n.type_parameters, n.visibility, n.is_exported, n.is_abstract "
            f"FROM ancestors a JOIN nodes n ON n.id = a.id",
            (*node_ids, *_CONTAINER_HIERARCHY_KINDS, *_CONTAINER_HIERARCHY_KINDS),
        ).fetchall()

    @staticmethod
    def _contract_container_rows(
        con: sqlite3.Connection, node_ids: list[str]
    ) -> list[sqlite3.Row]:
        if not node_ids:
            return []
        id_ph = ",".join("?" * len(node_ids))
        container_ph = ",".join("?" * len(_CONTRACT_CONTAINER_KINDS))
        return con.execute(
            f"SELECT DISTINCT n.id, n.kind, n.start_line, n.end_line, n.signature, n.return_type, "
            f"n.type_parameters, n.visibility, n.is_exported, n.is_abstract "
            f"FROM edges e JOIN nodes n ON n.id = e.source "
            f"WHERE e.kind = 'contains' AND e.target IN ({id_ph}) "
            f"AND n.kind IN ({container_ph})",
            (*node_ids, *_CONTRACT_CONTAINER_KINDS),
        ).fetchall()

    @staticmethod
    def _file_metadata(con: sqlite3.Connection, owner_rows: list[sqlite3.Row]) -> dict[str, Any]:
        paths = sorted({str(r["file_path"]) for r in owner_rows if r["file_path"]})
        if not paths:
            return {
                "graph_file_size_bytes": 0,
                "graph_file_node_count": 0,
                "graph_file_error_count": 0,
            }
        path_ph = ",".join("?" * len(paths))
        rows = con.execute(
            f"SELECT size, node_count, errors FROM files WHERE path IN ({path_ph})",
            tuple(paths),
        ).fetchall()
        return {
            "graph_file_size_bytes": sum(int(r["size"] or 0) for r in rows),
            "graph_file_node_count": sum(int(r["node_count"] or 0) for r in rows),
            "graph_file_error_count": sum(
                CodeGraphAdapter._file_error_count(r["errors"]) for r in rows
            ),
        }

    @staticmethod
    def _file_error_count(value: Any) -> int:
        if value is None or str(value).strip() in {"", "[]", "{}"}:
            return 0
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return 1
        if isinstance(parsed, list):
            return len(parsed)
        if isinstance(parsed, dict):
            return len(parsed)
        return 1

    @staticmethod
    def _contract_metadata(
        owner_rows: list[sqlite3.Row], container_rows: list[sqlite3.Row]
    ) -> dict[str, Any]:
        owner_kinds = {str(r["kind"]) for r in owner_rows if r["kind"]}
        container_kinds = {str(r["kind"]) for r in container_rows if r["kind"]}
        all_rows = list(owner_rows) + list(container_rows)
        all_kinds = owner_kinds | container_kinds

        if "interface" in container_kinds and "method" in owner_kinds:
            surface = "interface_method"
        elif "interface" in all_kinds:
            surface = "interface"
        elif "protocol" in all_kinds:
            surface = "protocol"
        elif "trait" in all_kinds:
            surface = "trait"
        elif owner_kinds and container_kinds:
            surface = f"{sorted(container_kinds)[0]}_{sorted(owner_kinds)[0]}"
        elif owner_kinds:
            surface = sorted(owner_kinds)[0]
        else:
            surface = "unknown"

        abstract_or_interface = bool(all_kinds & {"interface", "protocol", "trait"}) or any(
            CodeGraphAdapter._truthy(r["is_abstract"]) for r in all_rows
        )
        exported = abstract_or_interface or any(
            CodeGraphAdapter._truthy(r["is_exported"])
            or str(r["visibility"] or "").lower() in {"public", "public_api", "exported"}
            for r in all_rows
        )
        has_signature = any(
            CodeGraphAdapter._nonempty(r["signature"])
            or CodeGraphAdapter._nonempty(r["return_type"])
            or CodeGraphAdapter._nonempty(r["type_parameters"])
            for r in owner_rows
        )
        return {
            "contract_surface_kind": surface,
            "is_exported_contract": exported,
            "is_abstract_or_interface_contract": abstract_or_interface,
            "has_signature_metadata": has_signature,
        }

    @staticmethod
    def _truthy(value: Any) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() not in {"", "0", "false", "none", "null"}

    @staticmethod
    def _nonempty(value: Any) -> bool:
        return value is not None and str(value).strip() != ""

    @staticmethod
    def _modify_impact_target_ids(con: sqlite3.Connection, node_ids: list[str]) -> list[str]:
        """Resolved nodes plus containment ancestors for graph-wide modify blast."""
        if not node_ids:
            return []
        out = list(node_ids)
        rows = CodeGraphAdapter._container_hierarchy_rows(con, node_ids)
        for r in rows:
            if r["id"] not in out:
                out.append(r["id"])
        return out

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

    def _impact_distribution(self, con: sqlite3.Connection, db_path: Path) -> list[int]:
        key = (str(db_path), os.path.getmtime(db_path))
        cached = self._impact_dist_cache.get(key)
        if cached is not None:
            return cached
        call_ph = ",".join("?" * len(_CALLABLE_KINDS))
        container_ph = ",".join("?" * len(_CONTRACT_CONTAINER_KINDS))
        edge_ph = ",".join("?" * len(_MODIFY_IMPACT_EDGE_KINDS))
        distribution = sorted(
            int(r["c"]) for r in con.execute(
                f"SELECT n.id, COUNT(DISTINCT e.source) AS c FROM nodes n "
                f"LEFT JOIN edges cedge ON cedge.kind = 'contains' AND cedge.target = n.id "
                f"LEFT JOIN nodes parent ON parent.id = cedge.source "
                f"AND parent.kind IN ({container_ph}) "
                f"LEFT JOIN edges e ON e.kind IN ({edge_ph}) "
                f"AND (e.target = n.id OR e.target = parent.id) "
                f"WHERE n.kind IN ({call_ph}) GROUP BY n.id",
                (*_CONTRACT_CONTAINER_KINDS, *_MODIFY_IMPACT_EDGE_KINDS, *_CALLABLE_KINDS),
            ).fetchall()
        )
        self._impact_dist_cache[key] = distribution
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


def _dependents_unavailable(freshness: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "graph_freshness": freshness,
        "dependent_files": [],
        "count": 0,
        "fallback_reason": reason,
    }
