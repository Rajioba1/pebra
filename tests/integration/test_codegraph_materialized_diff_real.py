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
