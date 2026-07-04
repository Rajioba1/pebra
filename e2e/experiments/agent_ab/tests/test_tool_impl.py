"""Tool implementations: path guard fails closed, file ops confined to the clone, advisory dispatch."""

from __future__ import annotations

import pytest

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
