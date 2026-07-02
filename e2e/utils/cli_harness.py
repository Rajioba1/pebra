"""CLI harness — the agent's ONLY door to PEBRA in the CLI lane.

Every call shells out to ``python -m pebra ...`` (mirrors tests/golden's subprocess pattern) and parses
stdout. This module deliberately does NOT import pebra — the whole point of the agent boundary is that
PEBRA is exercised as an external process (argv in, JSON out). The pure helpers ``_parse_json_stdout`` /
``_check_exit`` are unit-tested; the subprocess methods are exercised by the live e2e features.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENV_PY = _REPO_ROOT / ".venv" / "Scripts" / "python.exe"
DEFAULT_TIMEOUT_SECONDS = 120


class CLIError(RuntimeError):
    """A ``pebra`` CLI invocation failed (non-zero exit) or returned unparseable JSON."""


def _python() -> str:
    return str(_VENV_PY) if _VENV_PY.exists() else sys.executable


def _check_exit(returncode: int, cmd: list[str], stderr: str) -> None:
    if returncode != 0:
        raise CLIError(f"command {cmd!r} exited {returncode}\n--- stderr ---\n{stderr}")


def _parse_json_stdout(stdout: str, cmd: list[str]) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CLIError(f"command {cmd!r} did not emit valid JSON\n--- stdout ---\n{stdout}") from exc


def _run(args: list[str], *, extra_env: dict[str, str] | None = None,
         timeout: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    cmd = [_python(), "-m", "pebra", *args]
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    _check_exit(proc.returncode, cmd, proc.stderr)
    return proc


def _run_json(args: list[str], *, extra_env: dict[str, str] | None = None) -> dict:
    proc = _run(args, extra_env=extra_env)
    return _parse_json_stdout(proc.stdout, args)


def assess(
    request_path: Path | str,
    *,
    repo_root: Path | str,
    db: Path | str,
    extra_env: dict[str, str] | None = None,
) -> dict:
    return _run_json([
        "assess", str(request_path), "--json", "--repo-root", str(repo_root), "--db", str(db),
    ], extra_env=extra_env)


def record_outcome(
    assessment_id: str, status: str, *, repo_root: Path | str, db: Path | str,
    detail: dict | None = None,
) -> None:
    args = [
        "record-outcome", "--assessment-id", assessment_id, "--status", status,
        "--repo-root", str(repo_root), "--db", str(db),
    ]
    if detail is not None:
        args += ["--detail", json.dumps(detail)]
    _run(args)


def learn(assessment_id: str, *, repo_root: Path | str, db: Path | str) -> dict:
    return _run_json([
        "learn", "--assessment-id", assessment_id, "--json", "--repo-root", str(repo_root),
        "--db", str(db),
    ])


def verify(
    assessment_id: str, *, repo_root: Path | str, db: Path | str,
    completed_checks: list[str] | None = None, scope: str = "staged",
    dry_run_preview: bool = False,
) -> tuple[bool, dict]:
    """Post-edit envelope check. Returns ``(passed, payload)`` — ``passed`` is True iff the CLI exits 0
    (pre_commit_decision PROCEED). Exit 2 (envelope violated) is a legitimate verify result, NOT a
    harness error, so it is not raised; any other exit code is."""
    args = [
        "verify", "--assessment-id", assessment_id, "--scope", scope, "--json",
        "--repo-root", str(repo_root), "--db", str(db),
    ]
    for check in completed_checks or []:
        args += ["--completed-check", f"{check}=passed"]
    if dry_run_preview:
        args.append("--dry-run-preview")
    cmd = [_python(), "-m", "pebra", *args]
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=DEFAULT_TIMEOUT_SECONDS
    )
    if proc.returncode not in (0, 2):
        _check_exit(proc.returncode, cmd, proc.stderr)
    return proc.returncode == 0, _parse_json_stdout(proc.stdout, cmd)


def promote(*, repo_root: Path | str, db: Path | str) -> dict:
    return _run_json(["promote", "--json", "--repo-root", str(repo_root), "--db", str(db)])


def scorecard(*, repo_root: Path | str, db: Path | str) -> dict:
    return _run_json(["scorecard", "--json", "--repo-root", str(repo_root), "--db", str(db)])


def setup_graph(*, repo_root: Path | str) -> None:
    _run(["setup-graph", "--fix", "--repo-root", str(repo_root)])


def graph_node_counts(*, repo_root: Path | str) -> dict:
    """`pebra graph-stats --json` → {total, callable, csharp_callable}. Used by the A/B graph preflight
    for an independent graph-validity check (a 'fresh' index that indexed no nodes is a real failure)."""
    return _run_json(["graph-stats", "--json", "--repo-root", str(repo_root)])


def dashboard_proc(*, repo_root: Path | str, db: Path | str, port: int = 0) -> subprocess.Popen:
    """Start ``pebra dashboard`` as a long-running process. The caller reads stdout for the URL line and
    is responsible for teardown (see dashboard_harness)."""
    cmd = [
        _python(), "-u", "-m", "pebra", "dashboard", "--repo-root", str(repo_root), "--db", str(db),
        "--port", str(port),
    ]
    # PYTHONUNBUFFERED + -u: the URL line is print()ed before uvicorn.run() blocks, so it must flush
    # immediately or the reader never sees it (a pipe is block-buffered, unlike a tty).
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
