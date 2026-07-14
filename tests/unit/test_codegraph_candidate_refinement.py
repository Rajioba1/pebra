from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pebra.adapters.codegraph_candidate_refinement import CodeGraphCandidateRefinementAdapter
from pebra.adapters.codegraph_candidate_refinement import _CONTINUITY_EDGE_KINDS
from pebra.adapters.codegraph_candidate_refinement import _default_cache_root
from pebra.adapters.codegraph_adapter import _FANIN_EDGE_KINDS
from pebra.core.models import CandidateAction, GraphRiskScope


def _scope(language: str = "typescript") -> GraphRiskScope:
    return GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
        language=language,
    )


def _action(patch: str) -> CandidateAction:
    return CandidateAction(id="a1", label="rename", action_type="edit", proposed_patch=patch)


def _patch(alias: bool = True) -> str:
    added = "+export const oldName = newName;\n" if alias else ""
    count = 2 if alias else 1
    return (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        f"@@ -1 +1,{count} @@\n"
        "-export function oldName(): void {}\n"
        "+export function newName(): void {}\n"
        f"{added}"
    )


def _db(
    path: Path, *, before: bool, preserved: bool = True, signature: str = "(): void",
    alias_exported: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
          id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
          language TEXT, start_line INTEGER, end_line INTEGER,
          start_column INTEGER, end_column INTEGER, visibility TEXT,
          is_exported INTEGER, signature TEXT
        );
        CREATE TABLE edges (
          id TEXT PRIMARY KEY, source TEXT, target TEXT, kind TEXT, line INTEGER,
          metadata TEXT, provenance TEXT
        );
        """
    )
    if before:
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old", "function", "oldName", "oldName", "src/api.ts", "typescript", 1, 1, 0, 34, None, 1, "(): void"),
        )
    else:
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("new", "function", "newName", "newName", "src/api.ts", "typescript", 1, 1, 0, 34, None, 1, signature),
        )
        if preserved:
            con.execute(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("alias", "constant", "oldName", "oldName", "src/api.ts", "typescript", 2, 2, 0, 31, None, int(alias_exported), None),
            )
            con.execute(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
                ("e1", "file:src/api.ts", "new", "references", 2,
                 '{"confidence":0.95,"resolvedBy":"function-ref"}', None),
            )
    con.commit()
    con.close()


def test_safe_exported_binding_yields_scoped_structural_fact(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(indexer=indexer, cache_root=tmp_path / "cache").analyze(
        _action(_patch()), str(tmp_path), _scope()
    )

    assert result.status == "available"
    assert result.verified_patch_hash is None
    assert result.facts[0].owner_node_ids == ("real-owner",)
    assert result.facts[0].fact_kind == "exported_binding_continuity"
    assert result.language == "typescript"
    assert result.witness == "ecmascript"
    assert result.witness_version == "1"
    assert calls == 2


@pytest.mark.parametrize("language", ["pascal", "python"])
def test_unsupported_language_does_not_spend_an_index(
    tmp_path: Path, language: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(_root: Path) -> Path:
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported language must not index")

    result = CodeGraphCandidateRefinementAdapter(indexer=indexer).analyze(
        _action(_patch()), str(tmp_path), _scope(language)
    )

    assert result.status == "not_applicable"
    assert result.reason == f"no measured continuity witness for {language}"
    assert calls == 0


def test_java_callable_forwarder_yields_scoped_structural_fact(tmp_path: Path) -> None:
    before_source = "public static int oldName(int x) { return x + 1; }\n"
    after_source = (
        "public static int newName(int x) { return x + 1; }\n"
        "public static int oldName(int x) { return newName(x); }\n"
    )
    (tmp_path / "Api.java").write_text(before_source, encoding="utf-8")
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE nodes (
              id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
              language TEXT, start_line INTEGER, end_line INTEGER,
              start_column INTEGER, end_column INTEGER, visibility TEXT,
              is_exported INTEGER, signature TEXT
            );
            CREATE TABLE edges (
              id TEXT PRIMARY KEY, source TEXT, target TEXT, kind TEXT, line INTEGER,
              metadata TEXT, provenance TEXT
            );
            """
        )
        if calls == 1:
            con.execute(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old", "method", "oldName", "Api::oldName", "Api.java", "java", 1, 1,
                 0, len(before_source.rstrip()), "public", 0, "int (int x)"),
            )
        else:
            con.executemany(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("new", "method", "newName", "Api::newName", "Api.java", "java", 1, 1,
                     0, len(after_source.splitlines()[0]), "public", 0, "int (int x)"),
                    ("old-wrapper", "method", "oldName", "Api::oldName", "Api.java", "java", 2, 2,
                     0, len(after_source.splitlines()[1]), "public", 0, "int (int x)"),
                ],
            )
            con.execute(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
                ("forward", "old-wrapper", "new", "calls", 2,
                 '{"confidence":0.95,"resolvedBy":"exact-match"}', None),
            )
        con.commit()
        con.close()
        return path

    patch = (
        "diff --git a/Api.java b/Api.java\n"
        "--- a/Api.java\n+++ b/Api.java\n"
        "@@ -1 +1,2 @@\n"
        f"-{before_source}"
        f"+{after_source.splitlines()[0]}\n"
        f"+{after_source.splitlines()[1]}\n"
    )
    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-owner",),
        owner_file_paths=("Api.java",),
        owner_qualified_names=("Api::oldName",),
        language="java",
    )

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(patch), str(tmp_path), scope)

    assert result.status == "available"
    assert result.facts[0].fact_kind == "exported_binding_continuity"
    assert result.witness == "java"


def test_direct_alias_proof_allows_only_formatting_blank_after_declaration() -> None:
    patch = (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        "@@ -1 +1,3 @@\n"
        "-export function oldName(): void {}\n"
        "+export function newName(): void {}\n"
        "+export const oldName = newName;\n"
        "+\n"
    )

    assert CodeGraphCandidateRefinementAdapter._patch_is_exhaustive_direct_alias(
        patch, "oldName", "newName"
    ) is True


@pytest.mark.parametrize(
    "unsupported",
    [
        (
            "diff --git a/payload.bin b/payload.bin\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "GIT binary patch\n"
            "literal 1\nA\n"
        ),
        "diff --git a/src/api.ts b/src/api.ts\nold mode 100644\nnew mode 100755\n",
        (
            "diff --git a/src/old.ts b/src/new.ts\n"
            "similarity index 100%\nrename from src/old.ts\nrename to src/new.ts\n"
        ),
    ],
)
def test_direct_alias_proof_rejects_non_text_patch_operations(unsupported: str) -> None:
    alias_patch = (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        "@@ -1 +1,2 @@\n"
        "-export function oldName(): void {}\n"
        "+export function newName(): void {}\n"
        "+export const oldName = newName;\n"
    )

    assert CodeGraphCandidateRefinementAdapter._patch_is_exhaustive_direct_alias(
        alias_patch + unsupported, "oldName", "newName"
    ) is False


def test_direct_alias_proof_rejects_helper_rebinding_in_another_hunk() -> None:
    patch = (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        "@@ -1 +1,2 @@\n"
        "-export function oldName(): number { return helper(); }\n"
        "+export function newName(): number { return helper(); }\n"
        "+export const oldName = newName;\n"
        "diff --git a/src/helper.ts b/src/helper.ts\n"
        "--- a/src/helper.ts\n+++ b/src/helper.ts\n"
        "@@ -1 +1 @@\n"
        "-export function helper(): number { return 1; }\n"
        "+export function helper(): number { return 2; }\n"
    )

    assert CodeGraphCandidateRefinementAdapter._patch_is_exhaustive_direct_alias(
        patch, "oldName", "newName"
    ) is False


def test_full_adapter_rejects_alias_with_unrelated_binary_payload(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    patch = (
        _patch()
        + "diff --git a/payload.bin b/payload.bin\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "GIT binary patch\n"
        "literal 1\nA\n"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(patch), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_refinement_edge_kinds_match_primary_fanin_edge_kinds() -> None:
    assert set(_CONTINUITY_EDGE_KINDS) == set(_FANIN_EDGE_KINDS)


@pytest.mark.parametrize(
    "extra",
    [
        "export const oldName = new Proxy(newName, {});",
        "export const oldName = newName;\nexport const unrelatedRisk = fail();",
        "export const oldName = (...args: never[]) => newName(...args);",
    ],
)
def test_non_direct_or_extra_alias_patch_never_reduces_risk(
    tmp_path: Path, extra: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    added = extra.splitlines()
    patch = (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        f"@@ -1 +1,{1 + len(added)} @@\n"
        "-export function oldName(): void {}\n"
        "+export function newName(): void {}\n"
        + "".join(f"+{line}\n" for line in added)
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(patch), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_missing_old_public_name_prefilter_avoids_indexing(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        raise AssertionError("prefilter should avoid CodeGraph")

    result = CodeGraphCandidateRefinementAdapter(indexer=indexer, cache_root=tmp_path / "cache").analyze(
        _action(_patch(alias=False)), str(tmp_path), _scope()
    )

    assert result.status == "ambiguous"
    assert calls == 0


def test_incompatible_callable_signature_never_reduces_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1, signature="(value: string): number")
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_non_exported_alias_never_reduces_public_contract_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1, alias_exported=False)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_export_continuity_never_refines_schema_contract_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    scope = GraphRiskScope(
        event="api_contract_break",
        risk_source="schema_migration_risk",
        owner_node_ids=("real-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
    )
    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: (_ for _ in ()).throw(
            AssertionError("schema risk must not invoke export-continuity proof")
        ),
        cache_root=tmp_path / "cache",
    )

    result = adapter.analyze(_action(_patch()), str(tmp_path), scope)

    assert result.status == "not_applicable"


def test_same_signature_different_implementation_never_reduces_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    malicious = _patch().replace(
        "+export function newName(): void {}",
        "+export function newName(): void { throw new Error('changed'); }",
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(malicious), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def _multi_owner_scope() -> GraphRiskScope:
    return GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=0,
    )


def _multi_owner_patch(*, extra_body_change: bool = False) -> str:
    after_call = "newName(); console.log('changed');" if extra_body_change else "newName();"
    return (
        "diff --git a/src/api.ts b/src/api.ts\n"
        "--- a/src/api.ts\n+++ b/src/api.ts\n"
        "@@ -1 +1,2 @@\n"
        "-export function oldName(): void {}\n"
        "+export function newName(): void {}\n"
        "+export const oldName = newName;\n"
        "diff --git a/src/use.ts b/src/use.ts\n"
        "--- a/src/use.ts\n+++ b/src/use.ts\n"
        "@@ -1 +1 @@\n"
        "-export function useName(): void { oldName(); }\n"
        f"+export function useName(): void {{ {after_call} }}\n"
    )


def _multi_owner_db(
    path: Path,
    *,
    before: bool,
    external_continuity: bool = False,
    preserve_external_after: bool = True,
    external_metadata: str = '{"confidence":0.95,"resolvedBy":"function-call"}',
    external_after_metadata: str | None = None,
    external_edge_kind: str = "calls",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
          id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
          language TEXT, start_line INTEGER, end_line INTEGER,
          start_column INTEGER, end_column INTEGER, visibility TEXT,
          is_exported INTEGER, signature TEXT
        );
        CREATE TABLE edges (
          id TEXT PRIMARY KEY, source TEXT, target TEXT, kind TEXT, line INTEGER,
          metadata TEXT, provenance TEXT
        );
        """
    )
    if before:
        nodes = [
            ("old", "function", "oldName", "oldName", "src/api.ts", "typescript", 1, 1, 0, 999, None, 1, "(): void"),
            ("use-before", "function", "useName", "useName", "src/use.ts", "typescript", 1, 1, 0, 999, None, 0, "(): void"),
        ]
        edges = [
            ("call-before", "use-before", "old", "calls", 1, '{"confidence":0.95,"resolvedBy":"function-call"}', None),
        ]
        if external_continuity:
            nodes.append(
                ("external-before", "function", "external", "external", "src/external.ts", "typescript", 1, 1, 0, 999, None, 0, "(): void")
            )
            edges.append(
                (
                    "external-call-before", "external-before", "use-before", external_edge_kind, 1,
                    external_metadata, None,
                )
            )
    else:
        nodes = [
            ("new", "function", "newName", "newName", "src/api.ts", "typescript", 1, 1, 0, 999, None, 1, "(): void"),
            ("alias", "constant", "oldName", "oldName", "src/api.ts", "typescript", 2, 2, 0, 999, None, 1, None),
            ("use-after", "function", "useName", "useName", "src/use.ts", "typescript", 1, 1, 0, 999, None, 0, "(): void"),
        ]
        edges = [
            ("alias-ref", "file:src/api.ts", "new", "references", 2, '{"confidence":0.95,"resolvedBy":"function-ref"}', None),
            ("call-after", "use-after", "new", "calls", 1, '{"confidence":0.95,"resolvedBy":"function-call"}', None),
        ]
        if external_continuity:
            nodes.append(
                ("external-after", "function", "external", "external", "src/external.ts", "typescript", 1, 1, 0, 999, None, 0, "(): void")
            )
            if preserve_external_after:
                edges.append(
                    (
                        "external-call-after", "external-after", "use-after", external_edge_kind, 1,
                        external_after_metadata or external_metadata, None,
                    )
                )
    con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", nodes)
    con.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?)", edges)
    con.commit()
    con.close()


def test_multi_owner_mechanical_rename_migration_yields_one_scoped_fact(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: ("src/api.ts", "src/use.ts"),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), _multi_owner_scope())

    assert result.status == "available"
    assert result.facts[0].owner_node_ids == ("real-old", "real-use")


def test_external_callers_of_any_changed_owner_must_preserve_target_identity(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    (tmp_path / "src" / "external.ts").write_text(
        "export function external(): void { useName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1, external_continuity=True)
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts",
            "src/use.ts",
            "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), scope)

    assert result.status == "available"
    assert result.facts[0].owner_node_ids == ("real-old", "real-use")


@pytest.mark.parametrize("preserve_after", [True, False])
def test_instantiates_edge_continuity_is_enforced(
    tmp_path: Path, preserve_after: bool,
) -> None:
    (tmp_path / "src").mkdir()
    for name, text in {
        "api.ts": "export function oldName(): void {}\n",
        "use.ts": "export function useName(): void { oldName(); }\n",
        "external.ts": "export function external(): void { new useName(); }\n",
    }.items():
        (tmp_path / "src" / name).write_text(text, encoding="utf-8")
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(
            path,
            before=calls == 1,
            external_continuity=True,
            preserve_external_after=preserve_after,
            external_edge_kind="instantiates",
        )
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts", "src/use.ts", "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), scope)

    assert result.status == ("available" if preserve_after else "ambiguous")
    assert bool(result.facts) is preserve_after


def test_paired_exact_match_external_edges_preserve_existing_uncertainty(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    for name, text in {
        "api.ts": "export function oldName(): void {}\n",
        "use.ts": "export function useName(): void { oldName(); }\n",
        "external.ts": "export function external(): void { useName(); }\n",
    }.items():
        (tmp_path / "src" / name).write_text(text, encoding="utf-8")
    calls = 0
    low_confidence = '{"confidence":0.7,"resolvedBy":"exact-match"}'

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(
            path,
            before=calls == 1,
            external_continuity=True,
            external_metadata=low_confidence,
        )
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts", "src/use.ts", "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), scope)

    assert result.status == "available"
    assert result.facts[0].confidence == pytest.approx(0.95)


@pytest.mark.parametrize(
    "after_metadata",
    [
        '{"confidence":0.71,"resolvedBy":"exact-match"}',
        '{"confidence":0.99,"resolvedBy":"heuristic"}',
    ],
)
def test_changed_or_heuristic_external_edge_metadata_fails_closed(
    tmp_path: Path, after_metadata: str,
) -> None:
    (tmp_path / "src").mkdir()
    for name, text in {
        "api.ts": "export function oldName(): void {}\n",
        "use.ts": "export function useName(): void { oldName(); }\n",
        "external.ts": "export function external(): void { useName(); }\n",
    }.items():
        (tmp_path / "src" / name).write_text(text, encoding="utf-8")
    calls = 0
    low_confidence = '{"confidence":0.7,"resolvedBy":"exact-match"}'

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(
            path,
            before=calls == 1,
            external_continuity=True,
            external_metadata=low_confidence,
            external_after_metadata=after_metadata,
        )
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts", "src/use.ts", "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_missing_external_caller_continuity_never_reduces_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    (tmp_path / "src" / "external.ts").write_text(
        "export function external(): void { useName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(
            path,
            before=calls == 1,
            external_continuity=True,
            preserve_external_after=False,
        )
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts",
            "src/use.ts",
            "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_unrelated_external_caller_body_change_never_counts_as_continuity(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    (tmp_path / "src" / "external.ts").write_text(
        "export function external(): void { useName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1, external_continuity=True)
        return path

    patch = _multi_owner_patch() + (
        "diff --git a/src/external.ts b/src/external.ts\n"
        "--- a/src/external.ts\n+++ b/src/external.ts\n"
        "@@ -1 +1 @@\n"
        "-export function external(): void { useName(); }\n"
        "+export function external(): void { useName(); console.log('changed'); }\n"
    )
    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-old", "real-use"),
        owner_file_paths=("src/api.ts", "src/use.ts"),
        owner_qualified_names=("oldName", "useName"),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: (
            "src/api.ts", "src/use.ts", "src/external.ts",
        ),
        cache_root=tmp_path / "cache",
    ).analyze(_action(patch), str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_heuristic_caller_edge_never_becomes_high_confidence_fact(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1)
        if calls == 2:
            con = sqlite3.connect(path)
            con.execute(
                "UPDATE edges SET metadata = ? WHERE id = 'call-after'",
                ('{"confidence":0.99,"resolvedBy":"heuristic"}',),
            )
            con.commit()
            con.close()
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: ("src/api.ts", "src/use.ts"),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), _multi_owner_scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_lost_duplicate_call_edge_never_counts_as_continuity(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1)
        if calls == 1:
            con = sqlite3.connect(path)
            con.execute(
                "INSERT INTO edges VALUES (?,?,?,?,?,?,?)",
                (
                    "call-before-2", "use-before", "old", "calls", 1,
                    '{"confidence":0.95,"resolvedBy":"function-call"}', None,
                ),
            )
            con.commit()
            con.close()
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: ("src/api.ts", "src/use.ts"),
        cache_root=tmp_path / "cache",
    ).analyze(_action(_multi_owner_patch()), str(tmp_path), _multi_owner_scope())

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_multi_owner_migration_with_unrelated_body_change_fails_closed(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    (tmp_path / "src" / "use.ts").write_text(
        "export function useName(): void { oldName(); }\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _multi_owner_db(path, before=calls == 1)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer,
        context_files_fn=lambda _root, _scope: ("src/api.ts", "src/use.ts"),
        cache_root=tmp_path / "cache",
    ).analyze(
        _action(_multi_owner_patch(extra_body_change=True)),
        str(tmp_path),
        _multi_owner_scope(),
    )

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_dirty_working_tree_bytes_change_manifest_hash(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "api.ts"
    target.write_text("export function oldName(): void {}\n", encoding="utf-8")
    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: Path("missing"), cache_root=tmp_path / "cache"
    )

    first = adapter.manifest_hash(_action(_patch()), str(tmp_path), _scope())
    target.write_text("// dirty\nexport function oldName(): void {}\n", encoding="utf-8")
    second = adapter.manifest_hash(_action(_patch()), str(tmp_path), _scope())

    assert first != second


def test_context_cap_fails_closed_before_index(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: (_ for _ in ()).throw(AssertionError("must not index")),
        max_context_bytes=1,
        cache_root=tmp_path / "cache",
    )

    result = adapter.analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "unavailable"
    assert result.context_truncated is True


def test_context_expansion_failure_fails_closed_before_index(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )

    def context_files(_root: str, _scope: GraphRiskScope) -> tuple[str, ...]:
        raise RuntimeError("graph unavailable")

    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: (_ for _ in ()).throw(AssertionError("must not index")),
        context_files_fn=context_files,
        cache_root=tmp_path / "cache",
    )

    result = adapter.analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "unavailable"
    assert result.reason == "dependent graph context unavailable"
    assert result.retryable_infrastructure is True


def test_incomplete_consumer_population_never_reduces_risk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1)
        return path

    scope = GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
        expected_consumer_count=1,
    )
    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(_patch()), str(tmp_path), scope)

    assert result.status == "ambiguous"
    assert result.facts == ()


def test_cache_hit_reuses_only_conservative_ambiguity_without_reindexing(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls == 1, preserved=False)
        return path

    adapter = CodeGraphCandidateRefinementAdapter(indexer=indexer, cache_root=tmp_path / "cache")
    first = adapter.analyze(_action(_patch()), str(tmp_path), _scope())
    second = adapter.analyze(_action(_patch()), str(tmp_path), _scope())

    assert first.status == second.status == "ambiguous"
    assert second.cache_hit is True
    assert calls == 2


def test_cache_namespace_tracks_current_proof_contract(tmp_path: Path) -> None:
    adapter = CodeGraphCandidateRefinementAdapter(cache_root=tmp_path / "cache")

    assert adapter._cache_path(str(tmp_path), "manifest").parent.name == "v9"


def test_default_cache_root_never_uses_repository_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("PEBRA_CACHE_DIR", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        expected = tmp_path / "local" / "pebra" / "cache"
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        expected = tmp_path / "xdg" / "pebra"

    cache = _default_cache_root()

    assert cache == expected
    assert ".pebra" not in cache.parts


def test_corrupt_cache_is_ignored_and_rebuilt(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=calls % 2 == 1, preserved=False)
        return path

    adapter = CodeGraphCandidateRefinementAdapter(indexer=indexer, cache_root=tmp_path / "cache")
    adapter.analyze(_action(_patch()), str(tmp_path), _scope())
    cache = adapter._cache_path(
        str(tmp_path), adapter.manifest_hash(_action(_patch()), str(tmp_path), _scope())
    )
    cache.write_text("not-json", encoding="utf-8")

    result = adapter.analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "ambiguous"
    assert result.cache_hit is False
    assert calls == 4


def test_project_config_bytes_participate_in_manifest(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    config = tmp_path / "tsconfig.json"
    config.write_text('{"strict":true}\n', encoding="utf-8")
    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: Path("missing"), cache_root=tmp_path / "cache"
    )
    first = adapter.manifest_hash(_action(_patch()), str(tmp_path), _scope())
    config.write_text('{"strict":false}\n', encoding="utf-8")
    second = adapter.manifest_hash(_action(_patch()), str(tmp_path), _scope())

    assert first != second


def test_disk_cache_never_trusts_positive_risk_reducing_fact(tmp_path: Path) -> None:
    adapter = CodeGraphCandidateRefinementAdapter(cache_root=tmp_path / "cache")
    cache = adapter._cache_path(str(tmp_path), "forged")
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        "schema": 1,
        "evidence": {
            "status": "available",
            "facts": [{
                "fact_kind": "exported_binding_continuity",
                "event": "public_api_break",
                "risk_source": "graph_modify_risk",
                "owner_node_ids": ["owner"],
                "confidence": 1.0,
                "provenance": "forged",
            }],
        },
    }), encoding="utf-8")

    assert adapter._load_cache(cache) is None


def test_candidate_after_index_failure_is_retryable_infrastructure(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.SubprocessError("candidate source rejected")
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=True)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "unavailable"
    assert result.reason == "candidate after-graph unavailable"
    assert result.retryable_infrastructure is False


def test_identifier_migration_inside_string_is_not_structural_continuity() -> None:
    before = 'export function oldName(): void { console.log("oldName"); }\n'
    after = 'export function newName(): void { console.log("newName"); }\n'

    assert CodeGraphCandidateRefinementAdapter._identifier_only_migration(
        before, after, "oldName", "newName"
    ) is False


def test_identifier_migration_inside_comment_is_not_structural_continuity() -> None:
    before = "export function oldName(): void {} // oldName\n"
    after = "export function newName(): void {} // newName\n"

    assert CodeGraphCandidateRefinementAdapter._identifier_only_migration(
        before, after, "oldName", "newName"
    ) is False


def test_property_name_migration_is_not_structural_continuity() -> None:
    before = "export function oldName(): void { api.oldName(); }\n"
    after = "export function newName(): void { api.newName(); }\n"

    assert CodeGraphCandidateRefinementAdapter._identifier_only_migration(
        before, after, "oldName", "newName"
    ) is False


def test_regex_literal_migration_is_not_structural_continuity() -> None:
    before = "export function oldName(): RegExp { return /oldName/; }\n"
    after = "export function newName(): RegExp { return /newName/; }\n"

    assert CodeGraphCandidateRefinementAdapter._identifier_only_migration(
        before, after, "oldName", "newName"
    ) is False


def test_candidate_after_index_timeout_consumes_attempt(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    calls = 0

    def indexer(root: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.TimeoutExpired("codegraph", 1)
        path = root / ".codegraph" / "codegraph.db"
        _db(path, before=True)
        return path

    result = CodeGraphCandidateRefinementAdapter(
        indexer=indexer, cache_root=tmp_path / "cache"
    ).analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "unavailable"
    assert result.retryable_infrastructure is False


def test_before_index_infrastructure_failure_is_retryable(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.ts").write_text(
        "export function oldName(): void {}\n", encoding="utf-8"
    )
    adapter = CodeGraphCandidateRefinementAdapter(
        indexer=lambda _root: (_ for _ in ()).throw(
            subprocess.SubprocessError("engine unavailable")
        ),
        cache_root=tmp_path / "cache",
    )

    result = adapter.analyze(_action(_patch()), str(tmp_path), _scope())

    assert result.status == "unavailable"
    assert result.reason == "before-snapshot CodeGraph unavailable"
    assert result.retryable_infrastructure is True
