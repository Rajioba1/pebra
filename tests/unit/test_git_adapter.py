import subprocess

from pebra.adapters import git_adapter


def _commit(repo, message: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=PEBRA Test", "-c", "user.email=test@example.invalid",
            "commit", "-am", message,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_worktree_dirty_detects_tracked_and_untracked_changes(tmp_path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    _commit(tmp_path, "initial")

    assert git_adapter.worktree_dirty(str(tmp_path)) is False

    tracked.write_text("two\n", encoding="utf-8")
    assert git_adapter.worktree_dirty(str(tmp_path)) is True

    subprocess.run(["git", "-C", str(tmp_path), "checkout", "--", "tracked.txt"], check=True)
    assert git_adapter.worktree_dirty(str(tmp_path)) is False

    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    assert git_adapter.worktree_dirty(str(tmp_path)) is True


def test_worktree_dirty_returns_none_when_git_status_fails(tmp_path) -> None:
    assert git_adapter.worktree_dirty(str(tmp_path)) is None
