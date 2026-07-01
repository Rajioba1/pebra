"""Prepare an isolated, gitignored copy of a real external repo — never mutating the source checkout.

The source is a LOCAL git checkout pointed at by ``E2E_TEMPLATE_BLUEPRINT_REPO`` (e.g.
``C:\\Users\\RajLord_new\\Desktop\\avalonia_template``). We record its HEAD SHA, refuse a dirty source
unless ``E2E_ALLOW_DIRTY_SOURCE=1`` (the run still tests committed HEAD), and ``git clone`` it into
``e2e/out/repos/template_blueprint/<sha>``
so all scenario edits happen on the copy. Pure stdlib + git subprocess; no pebra import (boundary rule).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import stat
from dataclasses import dataclass
from pathlib import Path

_PEBRA_ROOT = Path(__file__).resolve().parents[3]
REPOS_DIR = _PEBRA_ROOT / "e2e" / "out" / "repos" / "template_blueprint"
ENV_VAR = "E2E_TEMPLATE_BLUEPRINT_REPO"
ALLOW_DIRTY_VAR = "E2E_ALLOW_DIRTY_SOURCE"


class ExternalRepoError(RuntimeError):
    """The external source repo is missing, not a git repo, or dirty without an override."""


@dataclass
class ExternalRepo:
    source_path: Path
    copy_path: Path
    head_sha: str
    dirty_source: bool


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _head(cwd: Path) -> str:
    proc = _git(cwd, "rev-parse", "HEAD")
    if proc.returncode != 0:
        raise ExternalRepoError(f"{cwd} is not a git repo: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _status(cwd: Path) -> str:
    proc = _git(cwd, "status", "--porcelain")
    if proc.returncode != 0:
        raise ExternalRepoError(f"git status failed in {cwd}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _ensure_under_repos_dir(path: Path) -> None:
    resolved = path.resolve()
    root = REPOS_DIR.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ExternalRepoError(f"refusing to delete external path outside {root}: {resolved}") from exc
    if resolved == root:
        raise ExternalRepoError(f"refusing to delete external repos root: {resolved}")


def _rmtree_onerror(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc_info[1].with_traceback(exc_info[2])


def _remove_cached_copy(path: Path) -> None:
    if path.exists():
        _ensure_under_repos_dir(path)
        shutil.rmtree(path, onerror=_rmtree_onerror)


def _assert_clean_at_head(path: Path, head_sha: str) -> None:
    actual = _head(path)
    if actual != head_sha:
        raise ExternalRepoError(f"{path} is at {actual}, expected {head_sha}")
    dirty = _status(path)
    if dirty:
        raise ExternalRepoError(f"{path} is dirty after clone/reset: {dirty}")


def clone_at_recorded_head(external: ExternalRepo, dest: Path | str) -> Path:
    """Clone ``external.source_path`` to ``dest`` and pin it to ``external.head_sha``.

    Used by every external lane arm so graph/control/build copies cannot drift apart if the source
    checkout moves during a run.
    """
    target = Path(dest)
    if target.exists():
        raise ExternalRepoError(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["git", "clone", "-q", str(external.source_path), str(target)],
        capture_output=True,
        text=True,
    )
    if clone.returncode != 0:
        raise ExternalRepoError(
            f"clone of {external.source_path} failed: {clone.stderr.strip()}"
        )
    checkout = _git(target, "checkout", "-q", external.head_sha)
    if checkout.returncode != 0:
        raise ExternalRepoError(
            f"checkout {external.head_sha} failed: {checkout.stderr.strip()}"
        )
    _assert_clean_at_head(target, external.head_sha)
    return target


def _prepare_copy_at_head(external: ExternalRepo) -> Path:
    target = external.copy_path
    if (target / ".git").exists():
        checkout = _git(target, "checkout", "-q", external.head_sha)
        if checkout.returncode != 0:
            _remove_cached_copy(target)
            return clone_at_recorded_head(external, target)
        reset = _git(target, "reset", "--hard", external.head_sha)
        if reset.returncode != 0:
            raise ExternalRepoError(f"reset {external.head_sha} failed: {reset.stderr.strip()}")
        clean = _git(target, "clean", "-xfd")
        if clean.returncode != 0:
            raise ExternalRepoError(f"clean cached clone failed: {clean.stderr.strip()}")
        _assert_clean_at_head(target, external.head_sha)
        return target
    _remove_cached_copy(target)
    return clone_at_recorded_head(external, target)


def source_repo_path() -> Path | None:
    val = os.environ.get(ENV_VAR)
    return Path(val) if val else None


def prepare_external_repo(source: Path | str | None = None) -> ExternalRepo:
    src = Path(source) if source is not None else source_repo_path()
    if src is None:
        raise ExternalRepoError(
            f"set {ENV_VAR}=<path to a local git checkout of template_blueprint>"
        )
    head_sha = _head(src)
    dirty = bool(_status(src))
    if dirty and os.environ.get(ALLOW_DIRTY_VAR) != "1":
        raise ExternalRepoError(
            f"source {src} has uncommitted changes — commit it or set {ALLOW_DIRTY_VAR}=1"
        )

    copy_path = REPOS_DIR / head_sha
    external = ExternalRepo(source_path=src, copy_path=copy_path, head_sha=head_sha, dirty_source=dirty)
    _prepare_copy_at_head(external)
    _assert_clean_at_head(copy_path, head_sha)
    return ExternalRepo(source_path=src, copy_path=copy_path, head_sha=head_sha, dirty_source=dirty)
