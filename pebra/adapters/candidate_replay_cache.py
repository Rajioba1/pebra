"""Ephemeral, integrity-checked storage for exact assessed candidate replay inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_ALGORITHM = "sha256-candidate-replay-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class CandidateReplayError(RuntimeError):
    pass


def _canonical(bundle: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CandidateReplayError("candidate replay bundle is not JSON serializable") from exc


class CandidateReplayCache:
    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self._root = Path(root)
        self._max_bytes = max(1, int(max_bytes))
        self._max_age_seconds = max(1, int(max_age_seconds))

    def store(self, bundle: dict[str, Any]) -> dict[str, Any]:
        payload = _canonical(bundle)
        if len(payload) > self._max_bytes:
            raise CandidateReplayError("candidate replay exceeds the size limit")
        digest = hashlib.sha256(payload).hexdigest()
        self._root.mkdir(parents=True, exist_ok=True)
        self._reap()
        target = self._root / f"{digest}.json"
        if not target.exists():
            fd, raw_tmp = tempfile.mkstemp(prefix=".candidate-", dir=self._root)
            tmp = Path(raw_tmp)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    tmp.chmod(0o600)
                except OSError:
                    pass
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return {"status": "available", "algorithm": _ALGORITHM, "digest": digest}

    def load(self, metadata: dict[str, Any]) -> dict[str, Any]:
        digest = self._validated_digest(metadata)
        path = self._root / f"{digest}.json"
        try:
            stat = path.stat()
            payload = path.read_bytes()
        except OSError as exc:
            raise CandidateReplayError("candidate replay cache entry is missing") from exc
        if time.time() - stat.st_mtime > self._max_age_seconds:
            raise CandidateReplayError("candidate replay cache entry has expired")
        if len(payload) > self._max_bytes or hashlib.sha256(payload).hexdigest() != digest:
            raise CandidateReplayError("candidate replay cache digest does not match")
        try:
            bundle = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateReplayError("candidate replay cache content is invalid") from exc
        if not isinstance(bundle, dict):
            raise CandidateReplayError("candidate replay cache content is invalid")
        return bundle

    def delete(self, metadata: dict[str, Any]) -> None:
        digest = self._validated_digest(metadata)
        (self._root / f"{digest}.json").unlink(missing_ok=True)
        (self._root / f"{digest}.consumed").unlink(missing_ok=True)

    def consume(self, metadata: dict[str, Any]) -> None:
        digest = self._validated_digest(metadata)
        source = self._root / f"{digest}.json"
        target = self._root / f"{digest}.consumed"
        try:
            # A fresh assessment may recreate identical content after an earlier
            # application failed. Spending that new source replaces the old
            # tombstone; the gate still binds authorization to the exact assessment.
            os.replace(source, target)
        except OSError as exc:
            if target.exists():
                raise CandidateReplayError("candidate replay has already been consumed") from exc
            raise CandidateReplayError("candidate replay cache entry is missing") from exc

    @staticmethod
    def _validated_digest(metadata: dict[str, Any]) -> str:
        if (
            not isinstance(metadata, dict)
            or metadata.get("status") != "available"
            or metadata.get("algorithm") != _ALGORITHM
            or not isinstance(metadata.get("digest"), str)
            or _DIGEST.fullmatch(metadata["digest"]) is None
        ):
            raise CandidateReplayError("candidate replay metadata is invalid")
        return metadata["digest"]

    def _reap(self) -> None:
        cutoff = time.time() - self._max_age_seconds
        try:
            entries = tuple(self._root.glob("*.json")) + tuple(
                self._root.glob("*.consumed")
            )
        except OSError:
            return
        for path in entries:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
