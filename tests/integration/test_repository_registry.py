"""Architecture AD-24 — repo resolution from a subdirectory + .pebra/ init."""

from __future__ import annotations

from pathlib import Path

from pebra.adapters.repository_registry import RepositoryRegistry


def _make_repo(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "src" / "auth").mkdir(parents=True)
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")


def test_resolves_repo_root_from_nested_subdir(tmp_path) -> None:
    _make_repo(tmp_path)
    reg = RepositoryRegistry()
    meta = reg.resolve(str(tmp_path / "src" / "auth"))
    assert Path(meta.repo_root) == tmp_path.resolve()


def test_repo_id_is_stable(tmp_path) -> None:
    _make_repo(tmp_path)
    reg = RepositoryRegistry()
    a = reg.resolve(str(tmp_path / "src"))
    b = reg.resolve(str(tmp_path))
    assert a.repo_id == b.repo_id
    assert a.repo_id.startswith("repo_")


def test_pebra_dir_is_created_and_gitignored(tmp_path) -> None:
    _make_repo(tmp_path)
    RepositoryRegistry().resolve(str(tmp_path / "src"))
    assert (tmp_path / ".pebra").is_dir()
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".pebra/" in gitignore
