from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pebra.adapters.codegraph_candidate_refinement import CodeGraphCandidateRefinementAdapter
from pebra.core.models import CandidateAction, GraphRiskScope


def _scope() -> GraphRiskScope:
    return GraphRiskScope(
        event="public_api_break",
        risk_source="graph_modify_risk",
        owner_node_ids=("real-owner",),
        owner_file_paths=("src/api.ts",),
        owner_qualified_names=("oldName",),
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
    path: Path, *, before: bool, preserved: bool = True, signature: str = "(): void"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
          id TEXT PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
          language TEXT, start_line INTEGER, end_line INTEGER, visibility TEXT,
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
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("old", "function", "oldName", "oldName", "src/api.ts", "typescript", 1, 1, None, 1, "(): void"),
        )
    else:
        con.execute(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("new", "function", "newName", "newName", "src/api.ts", "typescript", 1, 1, None, 1, signature),
        )
        if preserved:
            con.execute(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("alias", "constant", "oldName", "oldName", "src/api.ts", "typescript", 2, 2, None, 1, None),
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
    assert calls == 2


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
