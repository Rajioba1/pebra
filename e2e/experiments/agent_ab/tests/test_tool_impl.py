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


def test_write_file_rejects_truncated_large_file_overwrite_and_points_to_edit(tmp_path):
    target = tmp_path / "large.ts"
    original = "x" * (tool_impl._MAX_READ_BYTES + 1)  # noqa: SLF001
    target.write_text(original, encoding="utf-8")

    result = tool_impl.write_file("large.ts", "replacement", tmp_path)

    assert result == {"error": "existing file is too large to replace safely; use edit_file"}
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_replaces_unique_text_beyond_read_truncation_limit(tmp_path):
    target = tmp_path / "large.ts"
    target.write_text("x" * 70_000 + "\nconst oldValue = 1;\n", encoding="utf-8")

    result = tool_impl.edit_file(
        "large.ts", "const oldValue = 1;", "const newValue = 2;", tmp_path
    )

    assert result == {"ok": True}
    assert target.read_text(encoding="utf-8").endswith("const newValue = 2;\n")


@pytest.mark.parametrize("content", ["no match", "old old"])
def test_edit_file_requires_one_unique_match_by_default(tmp_path, content):
    target = tmp_path / "a.ts"
    target.write_text(content, encoding="utf-8")

    result = tool_impl.edit_file("a.ts", "old", "new", tmp_path)

    assert "error" in result
    assert target.read_text(encoding="utf-8") == content


def test_edit_file_replace_all_replaces_every_match(tmp_path):
    target = tmp_path / "a.ts"
    target.write_text("old + old", encoding="utf-8")

    result = tool_impl.edit_file("a.ts", "old", "new", tmp_path, replace_all=True)

    assert result == {"ok": True}
    assert target.read_text(encoding="utf-8") == "new + new"


def test_edit_file_rejects_empty_old_string_and_hidden_path(tmp_path):
    target = tmp_path / "a.ts"
    target.write_text("value", encoding="utf-8")
    (tmp_path / ".pebra").mkdir()
    (tmp_path / ".pebra" / "state.ts").write_text("value", encoding="utf-8")

    assert "error" in tool_impl.edit_file("a.ts", "", "new", tmp_path)
    assert "error" in tool_impl.edit_file(".pebra/state.ts", "value", "new", tmp_path)
    assert target.read_text(encoding="utf-8") == "value"


def test_apply_patch_updates_multiple_files_atomically(tmp_path):
    (tmp_path / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const b = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-export const a = 1;
+export const a = 2;
diff --git a/b.ts b/b.ts
--- a/b.ts
+++ b/b.ts
@@ -1 +1 @@
-export const b = 1;
+export const b = 2;
"""

    assert tool_impl.apply_patch(patch, tmp_path) == {"ok": True}
    assert (tmp_path / "a.ts").read_text(encoding="utf-8").replace("\r\n", "\n") == "export const a = 2;\n"
    assert (tmp_path / "b.ts").read_text(encoding="utf-8").replace("\r\n", "\n") == "export const b = 2;\n"


def test_apply_patch_failure_leaves_every_file_unchanged(tmp_path):
    (tmp_path / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
--- a/a.ts
+++ b/a.ts
@@ -1 +1 @@
-does not match
+export const a = 2;
"""

    assert "error" in tool_impl.apply_patch(patch, tmp_path)
    assert (tmp_path / "a.ts").read_text(encoding="utf-8") == "export const a = 1;\n"


def test_apply_patch_rejects_unsafe_paths(tmp_path):
    patch = """diff --git a/../../outside.ts b/../../outside.ts
--- a/../../outside.ts
+++ b/../../outside.ts
@@ -0,0 +1 @@
+unsafe
"""
    assert tool_impl.apply_patch(patch, tmp_path) == {"error": "patch contains an unsafe path"}


def test_apply_patch_rejects_unbound_file_mode_changes(tmp_path):
    (tmp_path / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
    patch = """diff --git a/a.ts b/a.ts
old mode 100644
new mode 100755
"""
    assert tool_impl.apply_patch(patch, tmp_path) == {
        "error": "patch contains unsupported file-mode changes"
    }


def test_mutation_tools_reject_harness_private_paths_but_protocol_remains_readable(tmp_path):
    protocol = tmp_path / ".agent-instructions" / "edit_protocol.md"
    protocol.parent.mkdir()
    protocol.write_text("instructions\n", encoding="utf-8")

    assert tool_impl.read_file(
        ".agent-instructions/edit_protocol.md", tmp_path
    )["content"].replace("\r\n", "\n") == "instructions\n"
    assert "error" in tool_impl.edit_file(
        ".agent-instructions/edit_protocol.md", "instructions", "tampered", tmp_path
    )
    patch = """diff --git a/.agent-instructions/edit_protocol.md b/.agent-instructions/edit_protocol.md
--- a/.agent-instructions/edit_protocol.md
+++ b/.agent-instructions/edit_protocol.md
@@ -1 +1 @@
-instructions
+tampered
"""
    assert tool_impl.apply_patch(patch, tmp_path) == {"error": "patch contains a protected path"}
    assert protocol.read_text(encoding="utf-8") == "instructions\n"


def test_apply_patch_rejects_hunk_path_not_declared_by_diff_header(tmp_path):
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")
    hidden = tmp_path / ".pebra" / "state.txt"
    hidden.parent.mkdir()
    hidden.write_text("state\n", encoding="utf-8")
    patch = """diff --git a/safe.txt b/safe.txt
--- a/safe.txt
+++ b/safe.txt
@@ -1 +1 @@
-safe
+changed
--- a/.pebra/state.txt
+++ b/.pebra/state.txt
@@ -1 +1 @@
-state
+tampered
"""

    assert tool_impl.apply_patch(patch, tmp_path) == {
        "error": "patch has invalid or undeclared file headers"
    }
    assert hidden.read_text(encoding="utf-8") == "state\n"


def test_apply_patch_rejects_rename_metadata_outside_declared_paths(tmp_path):
    hidden = tmp_path / ".pebra" / "state.txt"
    hidden.parent.mkdir()
    hidden.write_text("state\n", encoding="utf-8")
    patch = """diff --git a/safe.txt b/safe2.txt
similarity index 100%
rename from .pebra/state.txt
rename to .pebra/state2.txt
"""
    assert tool_impl.apply_patch(patch, tmp_path) == {
        "error": "patch has invalid or undeclared file headers"
    }
    assert hidden.is_file()


@pytest.mark.parametrize(("path", "content"), [
    ("src/messages.ts", "I ran git checkout -- src/messages.ts; the repo is clean (verified)."),
    ("src/messages.css", '.notice { content: "run git checkout -- src/messages.ts"; }'),
    ("src/messages.py", '"""Run git checkout -- src/messages.py when restoring this fixture."""\nX = 1\n'),
])
def test_write_file_is_content_neutral_and_leaves_quality_judgment_to_oracle(tmp_path, path, content):
    out = tool_impl.write_file(path, content, tmp_path)

    assert out == {"ok": True}
    assert (tmp_path / path).read_text(encoding="utf-8") == content


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
            result = _result()
            result.tests_selected = 3
            result.targeted = True
            return result

    build = tool_impl.run_build(tmp_path, backend=FakeBackend())
    tests = tool_impl.run_tests(tmp_path, backend=FakeBackend())

    assert blinding.scan_text(build["error_summary"]) == (False, ())
    assert blinding.scan_text(tests["error_summary"]) == (False, ())
    assert tests["tests_selected"] == 3
    assert tests["targeted"] is True


def test_build_and_test_receive_remaining_command_timeout(tmp_path):
    seen: list[int] = []

    class FakeBackend:
        def run_build(self, repo_root, spec):
            seen.append(spec.command_timeout)
            return SimpleNamespace(available=True, passed=True, error_summary="")

        def run_tests(self, repo_root, spec):
            seen.append(spec.command_timeout)
            return SimpleNamespace(available=True, passed=True, error_summary="")

    tool_impl.run_build(tmp_path, backend=FakeBackend(), timeout_seconds=7.9)
    tool_impl.run_tests(tmp_path, backend=FakeBackend(), timeout_seconds=6.2)

    assert seen == [7, 6]


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


def test_advisory_check_accepts_structured_candidate_edits():
    seen = {}

    def backend(payload):
        seen.update(payload)
        return {"recommended_decision": "revise_safer"}

    out = tool_impl.advisory_check({
        "target_file": "a.ts",
        "change_summary": "preserve compatibility",
        "candidate_edits": [{
            "path": "a.ts", "old_string": "old", "new_string": "new",
        }],
    }, backend)

    assert out["recommended_decision"] == "revise_safer"
    assert seen["candidate_edits"][0]["path"] == "a.ts"


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
    assert "proposed_patch or candidate_edits" in out["advisory"]


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
