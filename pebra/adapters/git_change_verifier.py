"""git_change_verifier (Phase-1 ChangeVerifier, Architecture §9) — actual post-edit diff summary.

Adapter: uses ``git_adapter`` to read the real diff, flags dependency/schema/migration changes by
path, and reruns the symbol classifier on the ACTUAL diff (AD-27 post-edit reclassification): for each
changed Python file it parses the HEAD version and the working-tree version, diffs the symbols, and
classifies them with ``core/change_classifier``. The resulting ``actual_max_change_kind`` lets the
guardrails escalate when the committed change is more severe than the pre-edit packet.
"""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from pebra.adapters import git_adapter
from pebra.adapters.ast_diff_adapter import compute_symbol_diff_rows, parses
from pebra.core import change_classifier
from pebra.core.constants import ChangeKind
from pebra.core.language_capability import classify_tier
from pebra.core.models import ActualDiffSummary

_DEPENDENCY_GLOBS = (
    "*requirements*.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
)

_STRUCTURAL_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".vb", ".java", ".kt", ".kts", ".go", ".rs",
    ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".swift", ".scala", ".dart",
    ".lua", ".luau", ".r", ".erl", ".ex", ".exs", ".sol", ".tf", ".vue", ".svelte", ".astro",
})


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_structural_source_file(path: str) -> bool:
    return Path(path).suffix.lower() in _STRUCTURAL_SOURCE_EXTS


class GitChangeVerifier:
    def __init__(
        self,
        fanin_lookup: Callable[[list[str], str], dict[str, float]] | None = None,
        structural_symbols_fn: Callable[[str, str | None, str | None, str], Any] | None = None,
        materialized_diff_fn: Callable[..., Any] | None = None,
        language_capability_fn: Callable[[str, str], Any] | None = None,
        semantic_diff_enabled: bool = False,
        complexity_delta_fn: Callable[[str, str | None, str | None], tuple[float, float] | None]
        | None = None,
    ) -> None:
        # Injected by composition (bound to CodeGraphAdapter.percentiles_by_name). None -> verify keeps
        # the pre-A1 behavior (callers_percentile stays 0.0, no fan-in escalation). No adapter→adapter
        # import: the verifier depends only on the callable shape.
        self._fanin_lookup = fanin_lookup
        # Multi-language verify tier: bound to CodeGraphAdapter.structural_symbols. When wired, NON-Python
        # changed files are reclassified from graph structure instead of being silently skipped. None ->
        # the pre-multilang behavior (non-.py files ignored). Same callable-shape-only dependency.
        self._structural_symbols_fn = structural_symbols_fn
        # Semantic reproduction (symmetry with assess): bound to CodeGraphMaterializedDiffAdapter.diff.
        # When wired, deployment-enabled, and the `codegraph_semantic_diff_enabled` threshold is on, a
        # non-Python source file is reclassified with the SAME before/after signature diff assess used.
        # None / either gate off -> the coarse structural tier (dark).
        self._materialized_diff_fn = materialized_diff_fn
        self._language_capability_fn = language_capability_fn
        self._semantic_diff_enabled = semantic_diff_enabled
        # Benefit measurement (RCA): bound to RustCodeAnalysisAdapter.measure_delta. Returns
        # (complexity_delta, maintainability_index_delta) for an RCA-supported file, else None. Runs on
        # every read (Python + structural) file, BENEFIT-only — it never touches classification/risk.
        # None -> the pre-RCA behavior (no measured benefit delta on the verify path).
        self._complexity_delta_fn = complexity_delta_fn

    def actual_diff(
        self, repo_root: str, scope: str, thresholds: dict[str, float] | None = None
    ) -> ActualDiffSummary:
        files = git_adapter.changed_files(repo_root, scope)
        lowered = [f.lower() for f in files]
        dependency_changed = any(
            fnmatch(_basename(f), pat) for f in files for pat in _DEPENDENCY_GLOBS
        )
        schema_changed = any(f.endswith(".sql") or "/schema" in f or f.startswith("schema/")
                             for f in lowered)
        migration_changed = any("migration" in f for f in lowered)

        (max_kind, changed_symbols, measured_deltas, analyzed, consequential, reason, _python_analyzed,
         actual_structure_tier) = self._reclassify(repo_root, files, scope, thresholds=thresholds)
        # measured_deltas is already {complexity_delta, maintainability_index_delta} (or {}) from RCA,
        # over EVERY measured language (not just Python). A net-zero delta is still recorded (keys
        # present), distinct from "nothing measured" ({}), for AD-29 benefit learning.
        return ActualDiffSummary(
            current_head=git_adapter.head_commit(repo_root),
            changed_files=files,
            dependency_changed=dependency_changed,
            schema_changed=schema_changed,
            migration_changed=migration_changed,
            actual_max_change_kind=max_kind,
            actual_changed_symbols=changed_symbols,
            actual_consequential_symbol_changed=consequential,
            actual_consequence_reason=reason,
            measured_benefit_deltas=measured_deltas,
            reclassification_attempted=analyzed,
            actual_structure_tier=actual_structure_tier,
        )

    def _semantic_rows(
        self, f: str, before: str | None, after: str | None, repo_root: str, ev: Any,
        thresholds: dict[str, float] | None,
    ) -> list[dict]:
        """Reproduce the assess-path semantic tier for one non-Python file: run the before/after
        materialized diff and ENRICH the coarse floor. Gated by deployment flag + request threshold +
        measured-full language support (symmetry with assess). Modify-only (both sides present); the
        REAL repo-relative filename ``f`` is used so CodeGraph selects the right language extractor.
        Fail-soft to [] so verify never breaks and simply falls back to the coarse tier."""
        if (
            self._materialized_diff_fn is None
            or not self._semantic_diff_enabled
            or not (thresholds or {}).get("codegraph_semantic_diff_enabled")
            or before is None
            or after is None
            # Mirror assess's gate: only spend the (subprocess) materialized diff when the graph
            # actually resolved this owner by LOCATION. An unresolved/stale/absent ev yields an empty
            # coarse floor, so the semantic result would be discarded — skip the wasted subprocess.
            or getattr(ev, "resolution_method", "unresolved") != "location"
        ):
            return []
        languages = tuple(getattr(ev, "resolved_languages", ()) or ())
        if self._language_capability_fn is not None:
            if len(languages) != 1:
                return []
            try:
                cap = self._language_capability_fn(languages[0], repo_root)
            except Exception:  # noqa: BLE001 - capability probing must fail closed to coarse tier
                return []
            if classify_tier(cap) != "full":
                return []
        try:
            result = self._materialized_diff_fn(
                before_files={f: before}, after_files={f: after}, repo_root=repo_root
            )
        except Exception:  # noqa: BLE001 - a materialized-diff failure must never break verify
            return []
        if not getattr(result, "available", False):
            return []
        rows = change_classifier.rows_from_materialized_graph_diff(result, ev)
        # Only claim the semantic tier when it actually ENRICHED (rows differ from the coarse floor);
        # a degraded-to-floor result is the coarse tier, so return [] and let the caller label it
        # codegraph_structural — keeps verify's tier label honest and symmetric with assess.
        if rows == change_classifier.rows_from_fanin(ev):
            return []
        return rows

    def _read_after(self, repo_root: str, scope: str, f: str) -> str | None:
        """The post-edit content of ``f`` for the scope: staged blob for ``staged``, else working tree."""
        if scope == "staged":
            return git_adapter.file_at_rev(repo_root, ":0", f)  # staged (index) blob
        after_path = Path(repo_root) / f
        return after_path.read_text(encoding="utf-8") if after_path.exists() else None

    def _reclassify(
        self, repo_root: str, files: list[str], scope: str,
        thresholds: dict[str, float] | None = None,
    ) -> tuple[str, list[str], dict[str, float], bool, bool, list[str], bool, str]:
        """Rerun the symbol classifier on the actual diff (AD-27) + measure the RCA benefit delta.

        Python files use the full AST symbol diff; non-Python files, when a structural-symbols lookup is
        wired, are reclassified from graph structure (the multi-language tier). INDEPENDENTLY, when a
        complexity-delta fn (RCA) is wired, EVERY read file (Python OR structural) is measured for a
        benefit (complexity + maintainability) delta — multi-language, BENEFIT-only, never affecting
        classification. Returns the classification + ``measured_deltas`` (summed complexity + averaged MI,
        or {} when nothing measured) + ``python_analyzed``.
        """
        rows: list[dict] = []
        cc_delta = 0.0
        mi_deltas: list[float] = []
        analyzed = False          # any reclassification attempted (Python OR structural)
        python_analyzed = False   # a Python file parsed cleanly -> complexity delta is real
        unparsable = False
        structural_unresolved = False
        structural_attempted = False  # a non-Python file went through the graph-structural tier
        used_semantic = False         # a non-Python file was reproduced at the semantic tier
        parsed_ok = False
        for f in files:
            is_py = f.endswith(".py")
            is_struct = self._structural_symbols_fn is not None and _is_structural_source_file(f)
            if not (is_py or is_struct):
                continue
            before = git_adapter.file_at_rev(repo_root, "HEAD", f)
            after = self._read_after(repo_root, scope, f)
            if is_py:
                # We attempted classification of a Python file. (If HEAD itself was already unparseable
                # this still reports attempted=True/UNKNOWN — safe-conservative.)
                analyzed = True
                if parses(before) and parses(after):
                    parsed_ok = True
                    python_analyzed = True
                    rows.extend(compute_symbol_diff_rows(before, after, f))
                else:
                    unparsable = True
            elif is_struct:
                # Multi-language reclassification for a non-Python changed file.
                ev = self._structural_symbols_fn(f, before, after, repo_root)
                # Only count the graph tier as actually USED when the graph was AVAILABLE (fresh) — the
                # non-Python analogue of Python's always-present ast. A fresh-but-unresolved change (e.g.
                # a DELETED in-scope file) then fails CLOSED (analyzed=True + no rows -> UNKNOWN +
                # reclassification_attempted -> the classification_failed guardrail escalates). A merely
                # absent/stale graph (freshness != fresh) is infra absence, NOT a change signal — it must
                # not force-escalate, and must not mislabel the tier as structural (it resolved nothing).
                if getattr(ev, "graph_freshness", "unknown") == "fresh":
                    analyzed = True
                    structural_attempted = True
                    if getattr(ev, "resolution_method", "unresolved") == "unresolved":
                        structural_unresolved = True
                semantic_rows = self._semantic_rows(f, before, after, repo_root, ev, thresholds)
                if semantic_rows:
                    used_semantic = True
                    rows.extend(semantic_rows)
                else:
                    rows.extend(change_classifier.rows_from_fanin(ev))
            # Independent BENEFIT measurement (RCA): multi-language incl. Python; measure_delta returns
            # None for an unsupported language / unmeasurable side, so this is fail-safe and never risk.
            if self._complexity_delta_fn is not None:
                d = self._complexity_delta_fn(f, before, after)
                if d is not None:
                    cc_delta += d[0]
                    mi_deltas.append(d[1])

        # Summed complexity + averaged MI delta over the files RCA measured on both sides ({} if none).
        measured_deltas: dict[str, float] = {}
        if mi_deltas:
            measured_deltas = {
                "complexity_delta": cc_delta,
                "maintainability_index_delta": sum(mi_deltas) / len(mi_deltas),
            }

        # Which tier the post-edit reclassification used (for assess/verify symmetry). Prefer the most
        # informative tier actually produced: semantic (reproduced) > structural > python > unavailable.
        if used_semantic:
            tier = "codegraph_semantic"
        elif structural_attempted:
            tier = "codegraph_structural"
        elif python_analyzed or unparsable:
            tier = "python_ast"
        else:
            tier = "unavailable"

        if unparsable or structural_unresolved:
            # Any file we attempted but could not classify means the whole multi-file envelope is
            # unproven. Do not let another file's rows mask that failure.
            return "UNKNOWN", [], measured_deltas, analyzed, False, [], python_analyzed, tier
        if rows:
            self._enrich_fanin(rows, repo_root)
            summary = change_classifier.classify_diff(rows, thresholds or {})
            return (summary.max_change_kind, summary.changed_symbols, measured_deltas, analyzed,
                    summary.consequential_symbol_changed, list(summary.consequence_reason),
                    python_analyzed, tier)
        if parsed_ok:
            # parsed cleanly with no semantic change (docstring/comment/whitespace only) -> cosmetic
            return (ChangeKind.COSMETIC.value, [], measured_deltas, analyzed, False, [],
                    python_analyzed, tier)
        # nothing analyzed (pure non-code change, or non-Python with no structural lookup wired)
        return "UNKNOWN", [], measured_deltas, analyzed, False, [], python_analyzed, tier

    def _enrich_fanin(self, rows: list[dict], repo_root: str) -> None:
        """Fill ``callers_percentile`` per row from the graph engine (no-op when no lookup is wired or
        the engine returns nothing — the row keeps its conservative 0.0)."""
        if self._fanin_lookup is None or not rows:
            return
        try:
            percentiles = self._fanin_lookup([r["symbol_id"] for r in rows], repo_root)
        except Exception:  # never let fan-in lookup break verify (fail-soft, mirrors the assess path)
            return
        for r in rows:
            value = percentiles.get(r["symbol_id"])
            if value is not None:
                r["callers_percentile"] = value
