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
from pebra.adapters.codegraph_candidate_refinement import CodeGraphCandidateRefinementAdapter
from pebra.adapters.codegraph_temp_index import index_temp_tree
from pebra.core.engine_paths import find_engine
from pebra.core.models import CandidateAction, GraphRiskScope

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


_TS_CONTINUITY_PATCH = (
    "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n"
    "@@ -1 +1,2 @@\n"
    "-export function oldName(): void {}\n"
    "+export function newName(): void {}\n"
    "+export const oldName = newName;\n"
)


@requires_codegraph
def test_temp_index_overrides_inherited_codegraph_directory(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    root.mkdir()
    (root / "main.ts").write_text("export const answer = 42;\n", encoding="utf-8")
    monkeypatch.setenv("CODEGRAPH_DIR", ".codegraph-host")

    database = index_temp_tree(root)

    assert database == root / ".codegraph" / "codegraph.db"
    assert database.is_file()
    assert not (root / ".codegraph-host").exists()

_TS_SAME_SIGNATURE_SWAP_PATCH = (
    "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n"
    "@@ -1 +1,2 @@\n"
    "-export function oldName(): void {}\n"
    "+export function newName(): void { throw new Error('changed'); }\n"
    "+export const oldName = newName;\n"
)

_TS_ARROW_LITERAL_SWAP_PATCH = (
    "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n"
    "@@ -1 +1,2 @@\n"
    "-export const oldName = (): string => \"oldName\";\n"
    "+export const newName = (): string => \"newName\";\n"
    "+export const oldName = newName;\n"
)

_TS_MULTILINE_SOURCE = (
    "export function oldName(value: number): number {\n"
    "  const doubled = value * 2;\n"
    "  return doubled;\n"
    "}\n"
)

_TS_MULTILINE_CONTINUITY_PATCH = (
    "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n"
    "@@ -1,4 +1,5 @@\n"
    "-export function oldName(value: number): number {\n"
    "+export function newName(value: number): number {\n"
    "   const doubled = value * 2;\n"
    "   return doubled;\n"
    " }\n"
    "+export const oldName = newName;\n"
)

_TS_MULTILINE_BODY_SWAP_PATCH = (
    "diff --git a/src/api.ts b/src/api.ts\n--- a/src/api.ts\n+++ b/src/api.ts\n"
    "@@ -1,4 +1,5 @@\n"
    "-export function oldName(value: number): number {\n"
    "-  const doubled = value * 2;\n"
    "+export function newName(value: number): number {\n"
    "+  const doubled = value * 3;\n"
    "   return doubled;\n"
    " }\n"
    "+export const oldName = newName;\n"
)


def _multiline_scope() -> GraphRiskScope:
    return GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("original-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
    )


@requires_codegraph
def test_materialized_graph_proves_exported_binding_continuity(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    action = CandidateAction(
        id="rename", label="rename", action_type="edit", proposed_patch=_TS_CONTINUITY_PATCH
    )
    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("original-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), scope)

    assert result.status == "available", result.reason
    assert result.facts[0].owner_node_ids == ("original-owner",)
    assert result.facts[0].fact_kind == "exported_binding_continuity"


@requires_codegraph
def test_materialized_graph_proves_multiline_function_continuity(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(_TS_MULTILINE_SOURCE, encoding="utf-8")
    action = CandidateAction(
        id="multiline-rename",
        label="rename multiline function",
        action_type="edit",
        proposed_patch=_TS_MULTILINE_CONTINUITY_PATCH,
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), _multiline_scope())

    assert result.status == "available", result.reason
    assert result.facts[0].fact_kind == "exported_binding_continuity"


@requires_codegraph
def test_materialized_graph_rejects_multiline_function_body_change(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(_TS_MULTILINE_SOURCE, encoding="utf-8")
    action = CandidateAction(
        id="multiline-swap",
        label="change multiline implementation",
        action_type="edit",
        proposed_patch=_TS_MULTILINE_BODY_SWAP_PATCH,
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), _multiline_scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


@requires_codegraph
def test_materialized_graph_rejects_same_signature_implementation_swap(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    action = CandidateAction(
        id="swap", label="swap", action_type="edit",
        proposed_patch=_TS_SAME_SIGNATURE_SWAP_PATCH,
    )
    scope = GraphRiskScope(
        event="public_api_break", risk_source="graph_modify_risk",
        owner_node_ids=("original-owner",), owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


@requires_codegraph
def test_materialized_graph_rejects_arrow_body_name_normalization_trick(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        'export const oldName = (): string => "oldName";\n', encoding="utf-8"
    )
    action = CandidateAction(
        id="arrow-swap", label="arrow-swap", action_type="edit",
        proposed_patch=_TS_ARROW_LITERAL_SWAP_PATCH,
    )
    scope = GraphRiskScope(
        event="public_api_break", risk_source="graph_modify_risk",
        owner_node_ids=("original-owner",), owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


@pytest.mark.parametrize(
    ("language", "file_path", "qualified_name", "before_source", "patch"),
    [
        (
            "java",
            "Api.java",
            "Api::oldName",
            "public class Api {\n  public static int oldName(int x) { return x + 1; }\n}\n",
            "diff --git a/Api.java b/Api.java\n--- a/Api.java\n+++ b/Api.java\n"
            "@@ -1,3 +1,4 @@\n public class Api {\n"
            "-  public static int oldName(int x) { return x + 1; }\n"
            "+  public static int newName(int x) { return x + 1; }\n"
            "+  public static int oldName(int x) { return newName(x); }\n }\n",
        ),
        (
            "rust",
            "lib.rs",
            "old_name",
            "pub fn old_name(x: i32) -> i32 { x + 1 }\n",
            "diff --git a/lib.rs b/lib.rs\n--- a/lib.rs\n+++ b/lib.rs\n"
            "@@ -1 +1,2 @@\n-pub fn old_name(x: i32) -> i32 { x + 1 }\n"
            "+pub fn new_name(x: i32) -> i32 { x + 1 }\n"
            "+pub fn old_name(x: i32) -> i32 { new_name(x) }\n",
        ),
        (
            "go",
            "api.go",
            "OldName",
            "package sample\nfunc OldName(x int) int { return x + 1 }\n",
            "diff --git a/api.go b/api.go\n--- a/api.go\n+++ b/api.go\n"
            "@@ -1,2 +1,3 @@\n package sample\n"
            "-func OldName(x int) int { return x + 1 }\n"
            "+func NewName(x int) int { return x + 1 }\n"
            "+func OldName(x int) int { return NewName(x) }\n",
        ),
        (
            "dart",
            "api.dart",
            "oldName",
            "int oldName(int x) => x + 1;\n",
            "diff --git a/api.dart b/api.dart\n--- a/api.dart\n+++ b/api.dart\n"
            "@@ -1 +1,2 @@\n-int oldName(int x) => x + 1;\n"
            "+int newName(int x) => x + 1;\n"
            "+int oldName(int x) => newName(x);\n",
        ),
        (
            "scala",
            "Api.scala",
            "Api::oldName",
            "object Api {\n  def oldName(x: Int): Int = x + 1\n}\n",
            "diff --git a/Api.scala b/Api.scala\n--- a/Api.scala\n+++ b/Api.scala\n"
            "@@ -1,3 +1,4 @@\n object Api {\n"
            "-  def oldName(x: Int): Int = x + 1\n"
            "+  def newName(x: Int): Int = x + 1\n"
            "+  def oldName(x: Int): Int = newName(x)\n }\n",
        ),
    ],
)
@requires_codegraph
def test_materialized_graph_proves_callable_forwarder_continuity(
    tmp_path, language, file_path, qualified_name, before_source, patch
):
    (tmp_path / file_path).write_text(before_source, encoding="utf-8")
    action = CandidateAction(
        id=f"{language}-rename",
        label="rename with forwarding compatibility surface",
        action_type="edit",
        proposed_patch=patch,
    )
    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("original-owner",),
        owner_file_paths=(file_path,),
        owner_qualified_names=(qualified_name,),
        language=language,
    )

    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(action, str(tmp_path), scope)

    assert result.status == "available", result.reason
    assert result.language == language
    assert result.witness == language
    assert result.facts[0].fact_kind == "exported_binding_continuity"


@pytest.mark.parametrize(
    ("language", "file_path", "before_source", "patch"),
    [
        (
            "javascript",
            "api.js",
            "export function oldName(value) { return value + 1; }\n",
            "diff --git a/api.js b/api.js\n--- a/api.js\n+++ b/api.js\n"
            "@@ -1 +1,2 @@\n-export function oldName(value) { return value + 1; }\n"
            "+export function newName(value) { return value + 1; }\n"
            "+export const oldName = newName;\n",
        ),
        (
            "jsx",
            "Widget.jsx",
            "export function oldName(value) { return <span>{value}</span>; }\n",
            "diff --git a/Widget.jsx b/Widget.jsx\n--- a/Widget.jsx\n+++ b/Widget.jsx\n"
            "@@ -1 +1,2 @@\n-export function oldName(value) { return <span>{value}</span>; }\n"
            "+export function newName(value) { return <span>{value}</span>; }\n"
            "+export const oldName = newName;\n",
        ),
        (
            "tsx",
            "Widget.tsx",
            "export function oldName(value: string): JSX.Element { return <span>{value}</span>; }\n",
            "diff --git a/Widget.tsx b/Widget.tsx\n--- a/Widget.tsx\n+++ b/Widget.tsx\n"
            "@@ -1 +1,2 @@\n"
            "-export function oldName(value: string): JSX.Element { return <span>{value}</span>; }\n"
            "+export function newName(value: string): JSX.Element { return <span>{value}</span>; }\n"
            "+export const oldName = newName;\n",
        ),
    ],
)
@requires_codegraph
def test_materialized_graph_proves_ecmascript_family_continuity(
    tmp_path, language, file_path, before_source, patch
):
    (tmp_path / file_path).write_text(before_source, encoding="utf-8")
    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(
        CandidateAction(
            id=f"{language}-rename",
            label="rename with direct alias",
            action_type="edit",
            proposed_patch=patch,
        ),
        str(tmp_path),
        GraphRiskScope(
            event="public_api_break",
            risk_source="graph_modify_risk",
            owner_node_ids=("original-owner",),
            owner_file_paths=(file_path,),
            owner_qualified_names=("oldName",),
            language=language,
        ),
    )

    assert result.status == "available", result.reason
    assert result.witness == "ecmascript"


@requires_codegraph
def test_dart_signature_match_with_changed_body_does_not_prove_continuity(tmp_path):
    (tmp_path / "api.dart").write_text(
        "int oldName(int x) => x + 1;\n", encoding="utf-8"
    )
    patch = (
        "diff --git a/api.dart b/api.dart\n--- a/api.dart\n+++ b/api.dart\n"
        "@@ -1 +1,2 @@\n-int oldName(int x) => x + 1;\n"
        "+int newName(int x) => x - 1;\n"
        "+int oldName(int x) => newName(x);\n"
    )
    result = CodeGraphCandidateRefinementAdapter(
        cache_root=tmp_path / "host-cache"
    ).analyze(
        CandidateAction(
            id="dart-body-swap",
            label="rename and change behavior",
            action_type="edit",
            proposed_patch=patch,
        ),
        str(tmp_path),
        GraphRiskScope(
            event="public_api_break",
            risk_source="graph_modify_risk",
            owner_node_ids=("original-owner",),
            owner_file_paths=("api.dart",),
            owner_qualified_names=("oldName",),
            language="dart",
        ),
    )

    assert result.status == "ambiguous"
    assert result.facts == ()
