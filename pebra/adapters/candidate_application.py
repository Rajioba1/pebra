"""Transactional working-tree application of an already-authorized candidate patch."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath

from pebra.adapters.candidate_binding import materialize_candidate_patch
from pebra.core.patch_paths import is_safe_repo_path


class CandidateApplicationError(RuntimeError):
    pass


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CandidateApplicationAdapter:
    def __init__(self, *, replace_fn: Callable[[str | Path, str | Path], None] = os.replace):
        self._replace = replace_fn

    def lock(self, repo_root: str | Path) -> contextlib.AbstractContextManager[None]:
        root = Path(repo_root).resolve()
        return _file_lock(root / ".pebra" / "candidates" / "apply.lock")

    def apply(
        self,
        repo_root: str | Path,
        patch: str,
        *,
        expected_files: tuple[str, ...] | None = None,
        acquire_lock: bool = True,
    ) -> tuple[str, ...]:
        if acquire_lock:
            with self.lock(repo_root):
                return self.apply(
                    repo_root,
                    patch,
                    expected_files=expected_files,
                    acquire_lock=False,
                )
        root = Path(repo_root).resolve()
        after = materialize_candidate_patch(root, patch)
        if not after:
            raise CandidateApplicationError("candidate patch could not be materialized")
        if expected_files is not None:
            expected = {
                os.path.normcase(
                    PurePosixPath(value.replace("\\", "/")).as_posix()
                ).replace("\\", "/")
                for value in expected_files
                if is_safe_repo_path(value)
            }
            materialized = {
                os.path.normcase(rel).replace("\\", "/") for rel in after
            }
            if (
                len(expected) != len(expected_files)
                or len(materialized) != len(after)
                or materialized != expected
            ):
                raise CandidateApplicationError(
                    "materialized files do not match the validated candidate envelope"
                )
        return self._replace_transaction(root, after)

    def _replace_transaction(
        self, root: Path, after: dict[str, str | None]
    ) -> tuple[str, ...]:
        originals: dict[str, tuple[bytes | None, int | None]] = {}
        staged: dict[str, Path] = {}
        changed: list[str] = []
        try:
            for rel in sorted(after):
                target = root / rel
                try:
                    originals[rel] = (
                        target.read_bytes() if target.is_file() else None,
                        stat.S_IMODE(target.stat().st_mode) if target.exists() else None,
                    )
                except OSError as exc:
                    raise CandidateApplicationError(
                        f"candidate target could not be read: {rel}"
                    ) from exc
                content = after[rel]
                if content is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, raw_tmp = tempfile.mkstemp(prefix=".pebra-apply-", dir=target.parent)
                tmp = Path(raw_tmp)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                tmp.chmod(originals[rel][1] if originals[rel][1] is not None else 0o644)
                staged[rel] = tmp

            for rel in sorted(after):
                target = root / rel
                # Record before mutation so an exception raised by unlink/replace
                # still restores this target from the captured original.
                changed.append(rel)
                if after[rel] is None:
                    target.unlink()
                else:
                    self._replace(staged[rel], target)
                    staged.pop(rel, None)
        except Exception as exc:
            rollback_errors: list[str] = []
            for rel in reversed(changed):
                target = root / rel
                original, mode = originals[rel]
                try:
                    if original is None:
                        target.unlink(missing_ok=True)
                        continue
                    fd, raw_tmp = tempfile.mkstemp(prefix=".pebra-rollback-", dir=target.parent)
                    rollback = Path(raw_tmp)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(original)
                        handle.flush()
                        os.fsync(handle.fileno())
                    rollback.chmod(mode if mode is not None else 0o644)
                    self._replace(rollback, target)
                except Exception:  # noqa: BLE001 - report every failed restoration
                    rollback_errors.append(rel)
            if rollback_errors:
                raise CandidateApplicationError(
                    "candidate application failed and rollback was incomplete: "
                    + ", ".join(rollback_errors)
                ) from exc
            raise CandidateApplicationError(
                "candidate application failed and was rolled back"
            ) from exc
        finally:
            for path in staged.values():
                path.unlink(missing_ok=True)
        return tuple(sorted(after))
