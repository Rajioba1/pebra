"""Persistent, arm-neutral specimen workspaces for the live assay.

Each slot owns one long-lived clone, dependency tree, and CodeGraph index. A trial leases a slot,
resets source to the pinned specimen commit, removes prior trial artifacts, and preserves only the
expensive repository-local caches. The run's PEBRA database and traces remain outside the slot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from e2e.external.utils import repo_source as rs

SLOTS_ROOT = Path(__file__).resolve().parents[4] / "e2e" / "out" / "ab_slots"
WORKSPACE_LIFECYCLE_VERSION = "persistent-slots-v1"
_PRESERVED_DIRS = (".codegraph/", "node_modules/")


class SlotPoolError(RuntimeError):
    """A persistent slot could not be leased or restored to its pinned baseline."""


def assign_slots(
    arms: tuple[str, ...], *, run_id: str, task_id: str, seed: int
) -> dict[str, int]:
    """Assign unique slots with a deterministic run-specific rotation.

    Resume gets the same mapping, while a different run id rotates arms across physical slots so an
    arm cannot remain permanently correlated with one cached workspace.
    """
    if len(set(arms)) != len(arms):
        raise SlotPoolError("slot assignment requires unique arms")
    if not arms:
        return {}
    digest = hashlib.sha256(f"{run_id}:{task_id}:{seed}".encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % len(arms)
    return {arm: (position + offset) % len(arms) for position, arm in enumerate(arms)}


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _source_token(external: rs.ExternalRepo) -> str:
    source = str(external.source_path.resolve())
    payload = f"{source}\0{external.head_sha.lower()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _slot_token(external: rs.ExternalRepo, slot_index: int) -> str:
    payload = f"{_source_token(external)}:{slot_index}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if handle.read(1) == b"":
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise SlotPoolError("persistent assay slot is already in use") from exc


def _unlock(handle: BinaryIO) -> None:
    if handle.closed:
        return
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # type: ignore[import-not-found]  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _reset_repo(repo: Path, head_sha: str) -> None:
    reset = _run_git(repo, "reset", "--hard", head_sha)
    if reset.returncode != 0:
        raise SlotPoolError(f"could not reset persistent slot: {reset.stderr.strip()}")
    clean_args = ["clean", "-ffdx"]
    for preserved in _PRESERVED_DIRS:
        clean_args.extend(("-e", preserved))
    clean = _run_git(repo, *clean_args)
    if clean.returncode != 0:
        raise SlotPoolError(f"could not scrub persistent slot: {clean.stderr.strip()}")
    head = _run_git(repo, "rev-parse", "HEAD")
    status = _run_git(repo, "status", "--porcelain", "--untracked-files=all")
    if (
        head.returncode != 0
        or head.stdout.strip().lower() != head_sha.lower()
        or status.returncode != 0
        or status.stdout.strip()
    ):
        raise SlotPoolError("persistent slot did not return to the pinned clean HEAD")


def _clone(external: rs.ExternalRepo, repo: Path) -> None:
    source = (
        external.copy_path
        if (external.copy_path / ".git").is_dir()
        else external.source_path
    )
    clone_source = rs.ExternalRepo(
        source_path=source,
        copy_path=external.copy_path,
        head_sha=external.head_sha,
        dirty_source=False,
    )
    rs.clone_at_recorded_head(clone_source, repo)


def _rmtree_onerror(func, path, exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_repo(repo: Path) -> None:
    if repo.exists():
        shutil.rmtree(repo, onerror=_rmtree_onerror)
    if repo.exists():
        raise SlotPoolError("could not quarantine persistent slot repository")


def _next_generation(slot_dir: Path) -> int:
    path = slot_dir / "generation.txt"
    if path.exists():
        try:
            current = int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as exc:
            raise SlotPoolError("persistent slot generation is malformed") from exc
        if current < 1:
            raise SlotPoolError("persistent slot generation must be positive")
    else:
        current = 0
    generation = current + 1
    temporary = slot_dir / f"generation.{os.getpid()}.tmp"
    temporary.write_text(f"{generation}\n", encoding="ascii")
    os.replace(temporary, path)
    return generation


@dataclass
class SlotLease:
    repo_path: Path
    reused: bool
    slot_token: str
    generation: int
    _slot_dir: Path
    _handle: BinaryIO

    def release(self) -> None:
        _unlock(self._handle)

    def reset(self, external: rs.ExternalRepo) -> None:
        """Restore the locked slot to its source baseline while retaining expensive caches."""
        if self._handle.closed:
            raise SlotPoolError("cannot reset a released persistent slot")
        _reset_repo(self.repo_path, external.head_sha)

    def rebuild(self, external: rs.ExternalRepo) -> None:
        """Quarantine the current repository contents and recreate this locked slot."""
        if self._handle.closed:
            raise SlotPoolError("cannot rebuild a released persistent slot")
        if self.repo_path.parent.resolve() != self._slot_dir.resolve():
            raise SlotPoolError("persistent slot repository escaped its slot directory")
        _remove_repo(self.repo_path)
        _clone(external, self.repo_path)
        _reset_repo(self.repo_path, external.head_sha)
        self.reused = False

    def write_receipt(self, workspace: Path) -> None:
        """Bind a run artifact to this exact slot lease, not a future reuse."""
        workspace.mkdir(parents=True, exist_ok=True)
        payload = {
            "workspace_lifecycle_version": WORKSPACE_LIFECYCLE_VERSION,
            "repo_path": str(self.repo_path.resolve()),
            "slot_token": self.slot_token,
            "generation": self.generation,
        }
        path = workspace / "slot-receipt.json"
        temporary = workspace / f"slot-receipt.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)


def acquire_slot(
    external: rs.ExternalRepo,
    slot_index: int,
    *,
    slots_root: Path | None = None,
) -> SlotLease:
    if slot_index < 0:
        raise SlotPoolError("slot index must be non-negative")
    root = (slots_root or SLOTS_ROOT).resolve()
    token = _slot_token(external, slot_index)
    slot_dir = root / _source_token(external) / token
    slot_dir.mkdir(parents=True, exist_ok=True)
    handle = (slot_dir / "lease.lock").open("a+b")
    try:
        _lock(handle)
        repo = slot_dir / "repo"
        reused = repo.is_dir()
        if reused:
            try:
                _reset_repo(repo, external.head_sha)
            except SlotPoolError:
                _remove_repo(repo)
                _clone(external, repo)
                reused = False
        else:
            _clone(external, repo)
        _reset_repo(repo, external.head_sha)
        generation = _next_generation(slot_dir)
        return SlotLease(
            repo_path=repo,
            reused=reused,
            slot_token=token,
            generation=generation,
            _slot_dir=slot_dir,
            _handle=handle,
        )
    except Exception:
        _unlock(handle)
        raise
