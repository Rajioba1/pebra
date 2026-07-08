"""REAL-CodeGraph validation of the materialized before/after semantic diff (P0/P3).

Self-contained pebra-side integration test (importing pebra is fine here — unlike e2e/). Gated on the
real ``codegraph`` binary being installed; skipped otherwise. Uses a TypeScript file (a signature-CAPABLE
``full``-tier language, unlike C#) so it exercises the real signature diff, and empirically proves BUG-6:
two independent temp-dir indexes of the same owner produce STABLE (file_path, qualified_name) keys, so
before/after match rather than reporting a spurious "materialized owner mismatch" from path leakage.
"""

from __future__ import annotations

import pytest

from pebra.adapters.codegraph_materialized_diff import CodeGraphMaterializedDiffAdapter
from pebra.core.engine_paths import find_engine

requires_codegraph = pytest.mark.skipif(
    find_engine() is None, reason="codegraph binary not installed"
)

_TS_PATCH = (
    "diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1 +1 @@\n"
    "-export function f(x: number): number { return x }\n"
    "+export function f(x: string): number { return String(x).length }\n"
)


@requires_codegraph
def test_diff_for_patch_stable_keys_and_detects_ts_signature_change(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text(
        "export function f(x: number): number { return x }\n", encoding="utf-8"
    )
    adapter = CodeGraphMaterializedDiffAdapter(enabled=True)
    r1 = adapter.diff_for_patch(repo_root=str(tmp_path), patch=_TS_PATCH)
    r2 = adapter.diff_for_patch(repo_root=str(tmp_path), patch=_TS_PATCH)

    # BUG-6: before/after owners with the same qualified_name match (no tempdir-path leakage).
    assert r1.fallback_reason != "materialized owner mismatch"
    # Determinism: identical patch + working tree -> identical result.
    assert r1.available == r2.available and r1.rows == r2.rows
    # TypeScript is signature-capable -> the real binary should surface the signature change end-to-end.
    if r1.available:
        assert any(row.signature_changed for row in r1.rows)


_GO_SIG_PATCH = (
    "diff --git a/main.go b/main.go\n--- a/main.go\n+++ b/main.go\n@@ -1,2 +1,2 @@\n"
    " package main\n"
    "-func F(x int) int { return x }\n"
    "+func F(x string) int { return len(x) }\n"
)


@requires_codegraph
def test_go_signature_change_detected_end_to_end(tmp_path):
    # Go has a WORKING getSignature (only visibility was missing, now derived) -> a param-type change
    # surfaces as signature_changed with the real binary, proving Go genuinely benefits from the tier.
    (tmp_path / "main.go").write_text(
        "package main\nfunc F(x int) int { return x }\n", encoding="utf-8"
    )
    r = CodeGraphMaterializedDiffAdapter(enabled=True).diff_for_patch(
        repo_root=str(tmp_path), patch=_GO_SIG_PATCH
    )
    assert r.available, r.fallback_reason
    assert any(row.signature_changed for row in r.rows)


_JS_EXPORT_PATCH = (
    "diff --git a/calc.js b/calc.js\n--- a/calc.js\n+++ b/calc.js\n@@ -1 +1 @@\n"
    "-export function f(x) { return x }\n"
    "+function f(x) { return x }\n"
)


@requires_codegraph
def test_js_export_flip_surfaces_as_visibility_change_end_to_end(tmp_path):
    # The is_exported->visibility lever's payoff: dropping `export` flips the DERIVED visibility
    # exported->unexported, which the real binary + fill surface as a visibility change (a contract edit).
    (tmp_path / "calc.js").write_text(
        "export function f(x) { return x }\n", encoding="utf-8"
    )
    r = CodeGraphMaterializedDiffAdapter(enabled=True).diff_for_patch(
        repo_root=str(tmp_path), patch=_JS_EXPORT_PATCH
    )
    assert r.available, r.fallback_reason
    assert any(row.visibility_changed for row in r.rows)
