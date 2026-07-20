"""Isolated, version-fenced CodeGraph indexes for throwaway audit trees."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from pebra.adapters.bounded_process import run_bounded
from pebra.core.engine_argv import resolve_engine_argv
from pebra.core.engine_paths import find_engine
from pebra.core.graph_version import in_accepted_range, is_release_version


_INDEX_DIR_NAME = ".codegraph"
_STDOUT_LIMIT = 64 * 1024
_STDERR_LIMIT = 16 * 1024
_KNOWN_ENGINE_VERSIONS: dict[str, str] = {}
_KNOWN_ENGINE_VERSIONS_LOCK = threading.Lock()


class GraphEngineVersionRejected(subprocess.SubprocessError):
    """The resolved engine cannot supply trusted extraction semantics."""


class TempIndexIsolationError(subprocess.SubprocessError):
    """CodeGraph reported an index outside the isolated scratch cache."""


def _isolated_env() -> dict[str, str]:
    env = dict(os.environ)
    # CodeGraph accepts one directory name, not an absolute path. Override any inherited host/CI
    # choice so the throwaway tree always owns the exact cache PEBRA later opens.
    env["CODEGRAPH_DIR"] = _INDEX_DIR_NAME
    return env


def _invoke(
    executable: str,
    args: list[str],
    *,
    root: Path,
    timeout_s: float,
) -> str:
    result = run_bounded(
        resolve_engine_argv(executable, args),
        timeout=max(0.0, timeout_s),
        stdout_limit=_STDOUT_LIMIT,
        stderr_limit=_STDERR_LIMIT,
        cwd=str(root),
        env=_isolated_env(),
    )
    if (
        result.error is not None
        or result.returncode != 0
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        raise subprocess.SubprocessError("codegraph process failed")
    return result.stdout


def _normalized_release_version(value: object) -> str:
    if not is_release_version(value):
        raise GraphEngineVersionRejected("codegraph version rejected")
    assert isinstance(value, str)
    return value.removeprefix("v")


def read_engine_version(
    executable: str,
    *,
    root: Path | None = None,
    timeout_s: float = 10.0,
) -> str:
    """Read the actual bounded runtime version without assuming the install default."""
    cwd = (root or Path.cwd()).resolve()
    output = _invoke(executable, ["--version"], root=cwd, timeout_s=timeout_s)
    return _normalized_release_version(output.strip())


def known_engine_version(
    executable: str,
    *,
    root: Path | None = None,
    timeout_s: float = 10.0,
) -> str:
    """Return one stable measured version for this executable during the process lifetime."""
    key = str(Path(executable).resolve())
    with _KNOWN_ENGINE_VERSIONS_LOCK:
        known = _KNOWN_ENGINE_VERSIONS.get(key)
    if known is not None:
        return known
    measured = read_engine_version(executable, root=root, timeout_s=timeout_s)
    with _KNOWN_ENGINE_VERSIONS_LOCK:
        known = _KNOWN_ENGINE_VERSIONS.setdefault(key, measured)
    if known != measured:
        raise GraphEngineVersionRejected("codegraph version changed during process lifetime")
    return known


def index_temp_tree(root: Path, *, timeout_s: float = 30.0) -> Path:
    """Build one isolated temp index and reject untrusted or inconsistent engines."""
    root = root.resolve()
    executable = find_engine()
    if executable is None:
        raise FileNotFoundError("codegraph")
    deadline = time.monotonic() + max(0.0, timeout_s)

    version = known_engine_version(
        executable, root=root, timeout_s=max(0.0, deadline - time.monotonic())
    )
    if not in_accepted_range(version):
        raise GraphEngineVersionRejected("codegraph version rejected")

    _invoke(
        executable,
        ["init", str(root)],
        root=root,
        timeout_s=max(0.0, deadline - time.monotonic()),
    )
    status_text = _invoke(
        executable,
        ["status", str(root), "--json"],
        root=root,
        timeout_s=max(0.0, deadline - time.monotonic()),
    )
    try:
        status = json.loads(status_text)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise subprocess.SubprocessError("codegraph status invalid") from exc
    if not isinstance(status, dict):
        raise subprocess.SubprocessError("codegraph status invalid")
    status_version = _normalized_release_version(status.get("version"))
    if status_version != version or not in_accepted_range(status_version):
        raise GraphEngineVersionRejected("codegraph version rejected")

    expected_index = (root / _INDEX_DIR_NAME).resolve()
    reported_index = status.get("indexPath")
    if not isinstance(reported_index, str) or not reported_index.strip():
        raise TempIndexIsolationError("temporary index location unavailable")
    try:
        actual_index = Path(reported_index).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise TempIndexIsolationError("temporary index location invalid") from exc
    if actual_index != expected_index:
        raise TempIndexIsolationError("temporary index escaped scratch tree")

    database = expected_index / "codegraph.db"
    if not database.is_file():
        raise FileNotFoundError(str(database))
    return database
