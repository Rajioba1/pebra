from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.dev_package import (
    DevPackageError,
    dashboard_url_from_line,
    find_single_sdist,
    find_single_wheel,
    runtime_python,
    stage_source_tree,
)


def test_find_single_wheel_requires_exactly_one_artifact(tmp_path: Path) -> None:
    wheel = tmp_path / "pebra-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    assert find_single_wheel(tmp_path) == wheel

    (tmp_path / "pebra-0.1.1-py3-none-any.whl").write_bytes(b"other")
    with pytest.raises(DevPackageError, match="exactly one wheel"):
        find_single_wheel(tmp_path)


def test_find_single_wheel_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(DevPackageError, match="exactly one wheel"):
        find_single_wheel(tmp_path)


def test_find_single_sdist_requires_exactly_one_artifact(tmp_path: Path) -> None:
    sdist = tmp_path / "pebra-0.1.0.tar.gz"
    sdist.write_bytes(b"sdist")

    assert find_single_sdist(tmp_path) == sdist

    (tmp_path / "pebra-0.1.1.tar.gz").write_bytes(b"other")
    with pytest.raises(DevPackageError, match="exactly one sdist"):
        find_single_sdist(tmp_path)


def test_runtime_python_is_platform_specific(tmp_path: Path) -> None:
    assert runtime_python(tmp_path, platform="win32") == tmp_path / "Scripts" / "python.exe"
    assert runtime_python(tmp_path, platform="linux") == tmp_path / "bin" / "python"
    assert runtime_python(tmp_path, platform="darwin") == tmp_path / "bin" / "python"


def test_dashboard_url_parser_accepts_only_startup_line() -> None:
    assert dashboard_url_from_line(
        "PEBRA Risk Observatory: http://127.0.0.1:49152/?repo=abc"
    ) == "http://127.0.0.1:49152/?repo=abc"
    assert dashboard_url_from_line("unrelated output") is None


def test_stage_source_tree_uses_git_visible_files_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    staged = tmp_path / "staged"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("ignored.txt\nbuild/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("untracked", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "stale.txt").write_text("stale", encoding="utf-8")

    stage_source_tree(repo, staged)

    assert (staged / "tracked.txt").read_text(encoding="utf-8") == "tracked"
    assert not (staged / "untracked.txt").exists()
    assert not (staged / "ignored.txt").exists()
    assert not (staged / "build").exists()


def test_stage_source_tree_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=0, stdout=b"../escape.txt\0"
    )
    monkeypatch.setattr("scripts.dev_package.subprocess.run", lambda *args, **kwargs: completed)

    with pytest.raises(DevPackageError, match="unsafe source path"):
        stage_source_tree(tmp_path, tmp_path / "staged")


def test_nox_exposes_packaged_development_session() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "noxfile.py").read_text(encoding="utf-8")

    assert '@nox.session(name="dev-package")' in source
    assert '"python", "-m", "scripts.dev_package"' in source
