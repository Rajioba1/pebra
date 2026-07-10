"""Tool implementations: path guard fails closed, file ops confined to the clone, advisory dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from e2e.experiments.agent_ab.metrics import blinding
from e2e.experiments.agent_ab.runners import tool_impl


def test_resolve_guarded_blocks_traversal(tmp_path):
    with pytest.raises(tool_impl.PathTraversalError):
        tool_impl._resolve_guarded("../../etc/passwd", tmp_path)


def test_resolve_guarded_allows_in_repo(tmp_path):
    assert tool_impl._resolve_guarded("src/A.cs", tmp_path) == (tmp_path / "src/A.cs").resolve()


def test_write_then_read_roundtrip(tmp_path):
    assert tool_impl.write_file("src/A.cs", "hello", tmp_path) == {"ok": True}
    assert (tmp_path / "src/A.cs").read_text() == "hello"
    assert tool_impl.read_file("src/A.cs", tmp_path) == {"content": "hello"}


def test_write_file_rejects_prose_overwrite_for_typescript_source(tmp_path):
    src = tmp_path / "packages" / "zod" / "src" / "v3" / "types.ts"
    src.parent.mkdir(parents=True)
    src.write_text("export const existing = 1;\n", encoding="utf-8")

    out = tool_impl.write_file(
        "packages/zod/src/v3/types.ts",
        "Here is the fix. We should update the parser to handle this case and then run the tests.",
        tmp_path,
    )

    assert "error" in out
    assert "rejected" in out["error"]
    assert src.read_text(encoding="utf-8") == "export const existing = 1;\n"


def test_write_file_rejects_shell_script_overwrite_for_typescript_source(tmp_path):
    src = tmp_path / "packages" / "zod" / "src" / "v3" / "types.ts"
    src.parent.mkdir(parents=True)
    src.write_text("export const existing = 1;\n", encoding="utf-8")

    out = tool_impl.write_file(
        "packages/zod/src/v3/types.ts",
        "import { execSync } from 'node:child_process';\nexecSync('git checkout -- packages/zod/src/v3/types.ts');\n",
        tmp_path,
    )

    assert "error" in out
    assert "rejected" in out["error"]
    assert src.read_text(encoding="utf-8") == "export const existing = 1;\n"


def test_write_traversal_returns_error_not_raises(tmp_path):
    res = tool_impl.write_file("../../evil.txt", "x", tmp_path)
    assert "error" in res
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_read_missing_returns_error(tmp_path):
    assert "error" in tool_impl.read_file("nope.cs", tmp_path)


def test_list_dir_sorted_relative(tmp_path):
    (tmp_path / "b.cs").write_text("x")
    (tmp_path / "a.cs").write_text("x")
    out = tool_impl.list_dir(None, tmp_path)
    assert out["entries"] == ["a.cs", "b.cs"]


def test_file_tools_hide_codegraph_directory(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "codegraph.db").write_text("db")
    assert ".codegraph/" not in tool_impl.list_dir(None, tmp_path)["entries"]
    assert "error" in tool_impl.read_file(".codegraph/codegraph.db", tmp_path)
    assert "error" in tool_impl.write_file(".codegraph/codegraph.db", "x", tmp_path)


def test_file_tools_hide_pebra_directory(tmp_path):
    (tmp_path / ".pebra").mkdir()
    (tmp_path / ".pebra" / "pebra.db").write_text("needle")
    assert ".pebra/" not in tool_impl.list_dir(None, tmp_path)["entries"]
    assert "error" in tool_impl.read_file(".pebra/pebra.db", tmp_path)
    assert "error" in tool_impl.write_file(".pebra/pebra.db", "x", tmp_path)
    assert tool_impl.search_grep("needle", tmp_path)["matches"] == []


def test_hidden_pebra_write_error_is_safe_for_model(tmp_path):
    (tmp_path / ".pebra").mkdir()

    out = tool_impl.write_file(".pebra/notes.txt", "x", tmp_path)

    assert "error" in out
    assert blinding.scan_text(out["error"]) == (False, ())


def test_traversal_write_error_is_safe_for_model_when_path_contains_repo_name(tmp_path):
    repo = tmp_path / "pebra" / "repo"
    repo.mkdir(parents=True)
    outside = tmp_path / "pebra" / "outside.txt"

    out = tool_impl.write_file(str(outside), "x", repo)

    assert "error" in out
    assert blinding.scan_text(out["error"]) == (False, ())


def test_os_write_error_is_safe_for_model_when_repo_path_contains_repo_name(tmp_path, monkeypatch):
    repo = tmp_path / "pebra" / "repo"
    repo.mkdir(parents=True)

    def _raise(self, *_args, **_kwargs):
        raise OSError(123, "synthetic failure", str(self))

    monkeypatch.setattr(tool_impl.Path, "write_text", _raise)

    out = tool_impl.write_file("src/Gamma.cs", "x", repo)

    assert "error" in out
    assert blinding.scan_text(out["error"]) == (False, ())


def test_build_and_test_summaries_are_safe_for_model_when_paths_contain_repo_name(tmp_path, monkeypatch):
    def _result():
        return SimpleNamespace(
            available=True,
            passed=False,
            error_summary=str(tmp_path / "pebra" / "repo" / "src" / "Gamma.cs") + "(1,1): error CS1002",
        )

    class FakeBackend:
        def run_build(self, repo_root, spec):
            return _result()

        def run_tests(self, repo_root, spec):
            return _result()

    build = tool_impl.run_build(tmp_path, backend=FakeBackend())
    tests = tool_impl.run_tests(tmp_path, backend=FakeBackend())

    assert blinding.scan_text(build["error_summary"]) == (False, ())
    assert blinding.scan_text(tests["error_summary"]) == (False, ())


def test_search_grep_finds_match(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.cs").write_text("line one\nfind ME here\n")
    out = tool_impl.search_grep("find ME", tmp_path)
    assert any("x.cs" in m for m in out["matches"])


def test_search_grep_rejects_parent_glob_escape(tmp_path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("needle")
    out = tool_impl.search_grep("needle", tmp_path, file_glob="../*.txt")
    assert out["matches"] == []
    assert "error" in out


def test_search_grep_hides_codegraph(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "codegraph.db").write_text("needle")
    assert tool_impl.search_grep("needle", tmp_path)["matches"] == []


def test_advisory_check_dispatches_and_normalizes(tmp_path):
    def backend(_p):
        return {"recommended_decision": "proceed", "risk_level": "low", "advisory": "ok"}
    out = tool_impl.advisory_check({
        "target_file": "a.cs", "change_summary": "edit a", "proposed_patch": "diff --git a/a.cs b/a.cs",
    }, backend)
    assert out == {"recommended_decision": "proceed", "risk_level": "low", "advisory": "ok", "detail": {}}


def test_advisory_missing_patch_returns_arm_neutral_error():
    called = False

    def backend(_p):
        nonlocal called
        called = True
        return {"recommended_decision": "reject", "risk_level": "high", "advisory": "x"}

    out = tool_impl.advisory_check({"target_file": "a.cs", "change_summary": "edit a"}, backend)
    assert called is False
    assert out["recommended_decision"] is None
    assert out["risk_level"] == "unknown"
    assert out["detail"] == {}
    assert "proposed_patch" in out["advisory"]


def test_advisory_backend_exception_returns_arm_neutral_unavailable():
    def backend(_p):
        raise RuntimeError("backend unavailable")

    out = tool_impl.advisory_check({
        "target_file": "a.cs", "change_summary": "edit a", "proposed_patch": "diff --git a/a.cs b/a.cs",
    }, backend)

    assert out["recommended_decision"] is None
    assert out["risk_level"] == "unknown"
    assert out["detail"] == {}
    assert "unavailable" in out["advisory"]
