"""Unit tests for the shared Observatory launch context (Observatory TUI M1).

resolve_observatory_context() centralizes the read-only-vs-normal identity resolution that both the
`pebra dashboard` CLI and the future `pebra tui` CLI use, so neither surface re-implements (and drifts on)
the "--read-only never touches the repo" guarantee.
"""

from __future__ import annotations

import pytest

import pebra.observatory_context as octx
from pebra.observatory_context import ObservatoryContextError, resolve_observatory_context


def test_read_only_requires_db_and_repo_id() -> None:
    with pytest.raises(ObservatoryContextError):
        resolve_observatory_context(read_only=True, db=None, repo_id=None, repo_root=None)
    with pytest.raises(ObservatoryContextError):
        resolve_observatory_context(read_only=True, db="x.db", repo_id=None, repo_root=None)
    with pytest.raises(ObservatoryContextError):
        resolve_observatory_context(read_only=True, db=None, repo_id="r", repo_root=None)


def test_read_only_requires_existing_db(tmp_path) -> None:
    with pytest.raises(ObservatoryContextError):
        resolve_observatory_context(
            read_only=True, db=str(tmp_path / "missing.db"), repo_id="r", repo_root=None
        )


def test_read_only_passes_identity_through_without_resolving(tmp_path, monkeypatch) -> None:
    db = tmp_path / "pebra.db"
    db.write_bytes(b"placeholder")

    class _NoResolve:
        def resolve(self, *a, **k):
            raise AssertionError("read-only must NOT resolve a repo (would init .pebra)")

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _NoResolve())

    ctx = resolve_observatory_context(
        read_only=True, db=str(db), repo_id="repo_abc", repo_root="/graph/root"
    )
    assert ctx.db_path == str(db)
    assert ctx.repo_id == "repo_abc"
    assert ctx.repo_root == "/graph/root"  # kept as graph context, never resolved
    assert ctx.read_only is True


def test_normal_mode_resolves_repo_and_defaults_db(monkeypatch, tmp_path) -> None:
    root = tmp_path / "repo"

    class _Repo:
        repo_id = "resolved_repo"
        repo_root = str(root)

    class _Registry:
        def resolve(self, path):
            assert path == str(root)  # repo_root forwarded to resolution
            return _Repo()

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _Registry())

    ctx = resolve_observatory_context(read_only=False, db=None, repo_id=None, repo_root=str(root))
    assert ctx.repo_id == "resolved_repo"
    assert ctx.repo_root == str(root)
    assert ctx.db_path == str(root / ".pebra" / "pebra.db")
    assert ctx.read_only is False


def test_normal_mode_defaults_repo_root_to_cwd(monkeypatch) -> None:
    seen = {}

    class _Repo:
        repo_id = "r"
        repo_root = "/x"

    class _Registry:
        def resolve(self, path):
            seen["path"] = path
            return _Repo()

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _Registry())
    resolve_observatory_context(read_only=False, db=None, repo_id=None, repo_root=None)
    assert seen["path"] == "."  # None repo_root resolves the current directory, as today


def test_normal_mode_explicit_db_and_repo_id_win(monkeypatch) -> None:
    class _Repo:
        repo_id = "resolved_repo"
        repo_root = "/resolved/root"

    class _Registry:
        def resolve(self, path):
            return _Repo()

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _Registry())

    ctx = resolve_observatory_context(
        read_only=False, db="explicit.db", repo_id="explicit_repo", repo_root=None
    )
    assert ctx.db_path == "explicit.db"
    assert ctx.repo_id == "explicit_repo"
    assert ctx.repo_root == "/resolved/root"  # repo_root always comes from resolution in normal mode
