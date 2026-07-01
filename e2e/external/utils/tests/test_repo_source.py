"""External repo substrate must be pinned and clean; otherwise the graph/control arms can fake-pass."""

from __future__ import annotations

import subprocess

from e2e.external.utils import repo_source as rs


def _git(cwd, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _commit(cwd, message: str) -> str:
    subprocess.run(
        [
            "git", "-C", str(cwd),
            "-c", "user.name=PEBRA E2E", "-c", "user.email=e2e@example.invalid",
            "commit", "-am", message,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _git(cwd, "rev-parse", "HEAD")


def _source_repo(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
    (src / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "file.txt"], check=True)
    head = _commit(src, "initial")
    return src, head


def test_prepare_external_repo_resets_and_cleans_cached_clone(tmp_path, monkeypatch):
    src, head = _source_repo(tmp_path)
    monkeypatch.setattr(rs, "REPOS_DIR", tmp_path / "out")

    first = rs.prepare_external_repo(src)
    (first.copy_path / "file.txt").write_text("dirty\n", encoding="utf-8")
    (first.copy_path / "untracked.tmp").write_text("junk\n", encoding="utf-8")

    second = rs.prepare_external_repo(src)

    assert second.head_sha == head
    assert _git(second.copy_path, "rev-parse", "HEAD") == head
    assert _git(second.copy_path, "status", "--porcelain") == ""
    assert (second.copy_path / "file.txt").read_text(encoding="utf-8") == "one\n"
    assert not (second.copy_path / "untracked.tmp").exists()


def test_clone_at_recorded_head_pins_even_if_source_moves(tmp_path):
    src, head1 = _source_repo(tmp_path)
    external = rs.ExternalRepo(
        source_path=src, copy_path=tmp_path / "unused", head_sha=head1, dirty_source=False
    )
    (src / "file.txt").write_text("two\n", encoding="utf-8")
    head2 = _commit(src, "second")
    assert head2 != head1

    dest = tmp_path / "dest"
    rs.clone_at_recorded_head(external, dest)

    assert _git(dest, "rev-parse", "HEAD") == head1
    assert _git(dest, "status", "--porcelain") == ""
    assert (dest / "file.txt").read_text(encoding="utf-8") == "one\n"


def test_remove_cached_copy_uses_python_311_compatible_rmtree_callback(tmp_path, monkeypatch):
    root = tmp_path / "out"
    target = root / "cached"
    target.mkdir(parents=True)
    calls = []

    def fake_rmtree(path, **kwargs):
        calls.append((path, kwargs))

    monkeypatch.setattr(rs, "REPOS_DIR", root)
    monkeypatch.setattr(rs.shutil, "rmtree", fake_rmtree)

    rs._remove_cached_copy(target)

    assert calls == [(target, {"onerror": rs._rmtree_onerror})]
