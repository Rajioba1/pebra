"""Candidate bindings compare assessed patches with host edit payloads by resulting content."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pebra.adapters import candidate_binding
from pebra.core.models import CandidateAction


_PATCH = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _repo(tmp_path: Path) -> Path:
    path = tmp_path / "src" / "a.py"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@pebra.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "PEBRA Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def test_patch_and_claude_edit_produce_the_same_binding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assessed = candidate_binding.binding_for_patch(repo, _PATCH)
    attempted = candidate_binding.binding_for_event({
        "tool_name": "Edit",
        "cwd": str(repo),
        "tool_input": {
            "file_path": "src/a.py",
            "old_string": "old",
            "new_string": "new",
        },
    }, repo)

    assert assessed == attempted
    assert assessed["algorithm"] == "sha256-normalized-content-v1"
    assert set(assessed["files"]) == {"src/a.py"}


def test_write_and_multiedit_bind_to_resulting_content(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assessed = candidate_binding.binding_for_patch(repo, _PATCH)

    write = candidate_binding.binding_for_event({
        "tool_name": "Write", "cwd": str(repo),
        "tool_input": {"file_path": "src/a.py", "content": "new\r\n"},
    }, repo)
    multi = candidate_binding.binding_for_event({
        "tool_name": "MultiEdit", "cwd": str(repo),
        "tool_input": {"file_path": "src/a.py", "edits": [
            {"old_string": "old", "new_string": "mid"},
            {"old_string": "mid", "new_string": "new"},
        ]},
    }, repo)

    assert assessed == write == multi


def test_different_edit_does_not_reuse_assessed_candidate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assessed = candidate_binding.binding_for_patch(repo, _PATCH)
    attempted = candidate_binding.binding_for_event({
        "tool_name": "Edit", "cwd": str(repo),
        "tool_input": {"file_path": "src/a.py", "old_string": "old", "new_string": "other"},
    }, repo)

    assert assessed != attempted


def test_baseline_binding_changes_when_expected_file_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    action = CandidateAction(
        id="a1", label="edit", action_type="edit", expected_files=["src/a.py", "src/new.py"]
    )

    before = candidate_binding.baseline_binding_for_action(action, repo)
    (repo / "src" / "a.py").write_text("manual change\n", encoding="utf-8")
    after = candidate_binding.baseline_binding_for_action(action, repo)

    assert before is not None
    assert before != after
    assert before["algorithm"] == "sha256-git-worktree-v1"


def test_baseline_binding_covers_changes_outside_candidate_envelope(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    other = repo / "src" / "context.py"
    other.write_text("context\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "context"], cwd=repo, check=True)
    action = CandidateAction(
        id="a1", label="edit", action_type="edit", expected_files=["src/a.py"]
    )

    before = candidate_binding.baseline_binding_for_action(action, repo)
    other.write_text("changed context\n", encoding="utf-8")
    after = candidate_binding.baseline_binding_for_action(action, repo)

    assert before is not None
    assert before != after


def test_ambiguous_single_edit_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "a.py").write_text("old\nold\n", encoding="utf-8")
    event = {
        "tool_name": "Edit", "cwd": str(repo),
        "tool_input": {"file_path": "src/a.py", "old_string": "old", "new_string": "new"},
    }

    assert candidate_binding.binding_for_event(event, repo) is None


def test_apply_patch_event_uses_same_resulting_content_binding(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    command = (
        "*** Begin Patch\n"
        "*** Update File: src/a.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    event = {"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": command}}

    assert candidate_binding.binding_for_event(event, repo) == candidate_binding.binding_for_patch(repo, _PATCH)


def test_unsafe_or_outside_paths_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    event = {
        "tool_name": "Write", "cwd": str(repo),
        "tool_input": {"file_path": "../escape.py", "content": "x"},
    }

    assert candidate_binding.binding_for_event(event, repo) is None


def test_malformed_host_path_fails_closed_without_raising(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    event = {
        "tool_name": "Write", "cwd": str(repo),
        "tool_input": {"file_path": 42, "content": "x"},
    }

    assert candidate_binding.binding_for_event(event, repo) is None


def test_unencodable_host_content_fails_closed_without_raising(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    event = {
        "tool_name": "Write", "cwd": str(repo),
        "tool_input": {"file_path": "src/a.py", "content": "\ud800"},
    }

    assert candidate_binding.binding_for_event(event, repo) is None


def test_structured_edits_resolve_relative_paths_from_event_cwd(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assessed = candidate_binding.binding_for_patch(repo, _PATCH)
    cwd = repo / "src"
    events = [
        {"tool_name": "Write", "cwd": str(cwd),
         "tool_input": {"file_path": "a.py", "content": "new\n"}},
        {"tool_name": "Edit", "cwd": str(cwd),
         "tool_input": {"file_path": "a.py", "old_string": "old", "new_string": "new"}},
        {"tool_name": "MultiEdit", "cwd": str(cwd),
         "tool_input": {"file_path": "a.py", "edits": [
             {"old_string": "old", "new_string": "new"},
         ]}},
    ]

    assert all(candidate_binding.binding_for_event(event, repo) == assessed for event in events)


def test_create_and_delete_bindings_match_host_events(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    create = (
        "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+created\n"
    )
    delete = (
        "diff --git a/src/a.py b/src/a.py\ndeleted file mode 100644\n"
        "--- a/src/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    )
    write_event = {
        "tool_name": "Write", "cwd": str(repo),
        "tool_input": {"file_path": "src/new.py", "content": "created\n"},
    }
    delete_event = {
        "tool_name": "apply_patch", "cwd": str(repo),
        "tool_input": {"command": (
            "*** Begin Patch\n*** Delete File: src/a.py\n*** End Patch\n"
        )},
    }

    assert candidate_binding.binding_for_patch(repo, create) == candidate_binding.binding_for_event(
        write_event, repo
    )
    assert candidate_binding.binding_for_patch(repo, delete) == candidate_binding.binding_for_event(
        delete_event, repo
    )


def test_git_unified_apply_patch_event_matches_assessed_patch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    event = {"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": _PATCH}}

    assert candidate_binding.binding_for_event(event, repo) == candidate_binding.binding_for_patch(repo, _PATCH)


def test_mode_changing_patch_is_not_content_bindable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    mode_patch = _PATCH.replace("--- a/src/a.py", "old mode 100644\nnew mode 100755\n--- a/src/a.py")
    event = {"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": mode_patch}}

    assert candidate_binding.binding_for_patch(repo, mode_patch) is None
    assert candidate_binding.binding_for_event(event, repo) is None


def test_undeclared_hunk_path_is_not_bindable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hidden = repo / ".pebra" / "state.py"
    hidden.parent.mkdir()
    hidden.write_text("state\n", encoding="utf-8")
    smuggled = _PATCH + (
        "--- a/.pebra/state.py\n+++ b/.pebra/state.py\n@@ -1 +1 @@\n-state\n+tampered\n"
    )
    event = {"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": smuggled}}

    assert candidate_binding.binding_for_patch(repo, smuggled) is None
    assert candidate_binding.binding_for_event(event, repo) is None


def test_mixed_unified_and_codex_patch_formats_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    smuggled = _PATCH + (
        "*** Begin Patch\n"
        "*** Add File: src/smuggled.py\n"
        "+payload\n"
        "*** End Patch\n"
    )
    event = {
        "tool_name": "apply_patch",
        "cwd": str(repo),
        "tool_input": {"command": smuggled},
    }

    assert candidate_binding.binding_for_patch(repo, smuggled) is None
    assert candidate_binding.binding_for_event(event, repo) is None


def test_undeclared_rename_metadata_is_not_bindable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hidden = repo / ".pebra" / "state.py"
    hidden.parent.mkdir()
    hidden.write_text("state\n", encoding="utf-8")
    smuggled = (
        "diff --git a/src/a.py b/src/b.py\n"
        "similarity index 100%\n"
        "rename from .pebra/state.py\n"
        "rename to .pebra/state2.py\n"
    )
    event = {"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": smuggled}}
    assert candidate_binding.binding_for_patch(repo, smuggled) is None
    assert candidate_binding.binding_for_event(event, repo) is None


def test_git_unified_apply_patch_resolves_paths_from_event_cwd(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    command = _PATCH.replace("src/a.py", "a.py")
    event = {
        "tool_name": "apply_patch",
        "cwd": str(repo / "src"),
        "tool_input": {"command": command},
    }

    assert candidate_binding.binding_for_event(event, repo) == candidate_binding.binding_for_patch(
        repo, _PATCH
    )
