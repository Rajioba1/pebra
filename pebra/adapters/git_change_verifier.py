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

from pebra.adapters import git_adapter
from pebra.adapters.ast_diff_adapter import (
    compute_complexity_delta,
    compute_symbol_diff_rows,
    parses,
)
from pebra.core import change_classifier
from pebra.core.constants import ChangeKind
from pebra.core.models import ActualDiffSummary

_DEPENDENCY_GLOBS = (
    "*requirements*.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


class GitChangeVerifier:
    def __init__(
        self, fanin_lookup: Callable[[list[str], str], dict[str, float]] | None = None
    ) -> None:
        # Injected by composition (bound to CodeGraphAdapter.percentiles_by_name). None -> verify keeps
        # the pre-A1 behavior (callers_percentile stays 0.0, no fan-in escalation). No adapter→adapter
        # import: the verifier depends only on the callable shape.
        self._fanin_lookup = fanin_lookup

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

        max_kind, changed_symbols, complexity_delta, analyzed, consequential, reason = (
            self._reclassify(repo_root, files, scope, thresholds=thresholds)
        )
        # record the delta whenever Python files were analyzed — even a net-zero delta is signal
        # (distinct from "no Python files changed") for AD-29 benefit learning.
        measured_deltas = {"complexity_delta": complexity_delta} if analyzed else {}
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
        )

    def _reclassify(
        self, repo_root: str, files: list[str], scope: str,
        thresholds: dict[str, float] | None = None,
    ) -> tuple[str, list[str], float, bool, bool, list[str]]:
        """Rerun the symbol classifier on the actual diff (AD-27) + measure complexity delta.

        The "after" source must match the scope: for ``staged`` the actual diff is index-vs-HEAD, so
        we read the staged blob (``:0:path``); otherwise we read the working tree on disk.

        A1: when a fan-in lookup is wired, fill each row's ``callers_percentile`` from the graph engine
        BEFORE classifying, so the post-edit consequence verdict sees real per-symbol fan-in (symmetric
        with assess). Returns the consequential flag + reasons so the guardrail can escalate on it.
        """
        rows: list[dict] = []
        complexity_delta = 0.0
        analyzed = False
        unparsable = False
        parsed_ok = False
        for f in files:
            if not f.endswith(".py"):
                continue
            # We attempted classification of a Python file. (Diagnostic note: if HEAD itself was
            # already unparseable, this still reports attempted=True/UNKNOWN — safe-conservative, but
            # it does not distinguish "this edit broke parsing" from "HEAD was already broken".)
            analyzed = True
            before = git_adapter.file_at_rev(repo_root, "HEAD", f)
            if scope == "staged":
                after = git_adapter.file_at_rev(repo_root, ":0", f)  # staged (index) blob
            else:
                after_path = Path(repo_root) / f
                after = after_path.read_text(encoding="utf-8") if after_path.exists() else None
            if not (parses(before) and parses(after)):
                unparsable = True
                continue
            parsed_ok = True
            rows.extend(compute_symbol_diff_rows(before, after, f))
            complexity_delta += compute_complexity_delta(before, after)

        if rows:
            self._enrich_fanin(rows, repo_root)
            summary = change_classifier.classify_diff(rows, thresholds or {})
            return (summary.max_change_kind, summary.changed_symbols, complexity_delta, analyzed,
                    summary.consequential_symbol_changed, list(summary.consequence_reason))
        if unparsable:
            # changed Python we couldn't parse -> cannot prove envelope compliance (escalates)
            return "UNKNOWN", [], complexity_delta, analyzed, False, []
        if parsed_ok:
            # parsed cleanly with no semantic change (docstring/comment/whitespace only) -> cosmetic
            return ChangeKind.COSMETIC.value, [], complexity_delta, analyzed, False, []
        # no Python files analyzed at all (pure non-code change)
        return "UNKNOWN", [], complexity_delta, analyzed, False, []

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
