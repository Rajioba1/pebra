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
import tempfile
from pathlib import Path
from typing import Callable

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


def _run_json(
    args: list[str], *, extra_env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    proc = _run(args, extra_env=extra_env, timeout=timeout)
    return _parse_json_stdout(proc.stdout, args)


def assess(
    request_path: Path | str,
    *,
    repo_root: Path | str,
    db: Path | str,
    trusted_candidate_verification_path: Path | str | None = None,
    trusted_task_obligations_path: Path | str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    args = [
        "assess", str(request_path), "--json", "--repo-root", str(repo_root), "--db", str(db),
    ]
    if trusted_candidate_verification_path is not None:
        args += [
            "--trusted-candidate-verification-file",
            str(trusted_candidate_verification_path),
        ]
    if trusted_task_obligations_path is not None:
        args += ["--trusted-task-obligations-file", str(trusted_task_obligations_path)]
    return _run_json(args, extra_env=extra_env, timeout=timeout)


def candidate_patch(
    edits: list[dict], *, repo_root: Path | str, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> dict:
    """Convert structured edits through the production CLI without importing PEBRA."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"edits": edits}, fh)
        request_path = Path(fh.name)
    try:
        return _run_json([
            "candidate-patch", str(request_path), "--repo-root", str(repo_root), "--json",
        ], timeout=timeout)
    finally:
        request_path.unlink(missing_ok=True)


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


def accept_risk(
    sanction_spec: dict, *, repo_root: Path | str, db: Path | str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Create a production sanction through the CLI boundary from host-owned experiment evidence."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(sanction_spec, fh)
        spec_path = Path(fh.name)
    try:
        return _run_json([
            "accept-risk", str(spec_path), "--repo-root", str(repo_root), "--db", str(db),
        ], timeout=timeout)
    finally:
        spec_path.unlink(missing_ok=True)


def learn(assessment_id: str, *, repo_root: Path | str, db: Path | str) -> dict:
    return _run_json([
        "learn", "--assessment-id", assessment_id, "--json", "--repo-root", str(repo_root),
        "--db", str(db),
    ])


def verify(
    assessment_id: str, *, repo_root: Path | str, db: Path | str,
    completed_checks: dict[str, str] | None = None, scope: str = "staged",
    dry_run_preview: bool = False,
) -> tuple[bool, dict]:
    """Post-edit envelope check. Returns ``(passed, payload)`` — ``passed`` is True iff the CLI exits 0
    (pre_commit_decision PROCEED). Exit 2 (envelope violated) is a legitimate verify result, NOT a
    harness error, so it is not raised; any other exit code is."""
    args = [
        "verify", "--assessment-id", assessment_id, "--scope", scope, "--json",
        "--repo-root", str(repo_root), "--db", str(db),
    ]
    for check, status in (completed_checks or {}).items():
        if status not in {"passed", "failed"}:
            raise ValueError(f"invalid completed-check status for {check!r}: {status!r}")
        args += ["--completed-check", f"{check}={status}"]
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


def gate_check(
    event: dict, *, db: Path | str, consult_only: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """`pebra gate-check` — the pure pre-edit gate DECISION for a proposed edit. The host event
    (``tool_name``/``tool_input``/``cwd``) goes in on STDIN; a ``{permission, tier, reason, warn}`` JSON
    comes out. gate-check always exits 0 (allow/deny/ask as data) — the caller enforces. The event
    carries ``cwd=repo_root``; the store is the shared clone db written by ``pebra assess``.

    ``consult_only`` skips the ask verdict tier — the A/B runner has NO human approver, so an ``ask``
    would be an un-resolvable block that conflates "PEBRA escalated" with "no approver present"."""
    cmd = [
        _python(), "-m", "pebra", "gate-check", "--db", str(db),
        "--include-host-metadata",
    ]
    if consult_only:
        cmd.append("--consult-only")
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    proc = subprocess.run(cmd, input=json.dumps(event), capture_output=True, text=True,
                          env=env, timeout=timeout)
    _check_exit(proc.returncode, cmd, proc.stderr)
    return _parse_json_stdout(proc.stdout, cmd)


def _git_output(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


def _ensure_local_graph_excludes(repo_root: Path) -> None:
    proc = _git_output(repo_root, "rev-parse", "--git-path", "info/exclude")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CLIError(f"could not resolve git info/exclude: {proc.stderr.strip()}")
    exclude = Path(proc.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo_root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    additions = [entry for entry in (".pebra/", ".codegraph/") if entry not in lines]
    if not additions:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(
        existing + separator + "".join(f"{entry}\n" for entry in additions),
        encoding="utf-8",
    )


def run_source_neutral_graph_setup(
    repo_root: Path | str, setup_fn: Callable[[Path], None]
) -> None:
    """Run CodeGraph setup without adding repository-visible setup artifacts."""
    root = Path(repo_root)
    if not root.exists():
        setup_fn(root)
        return
    inside = _git_output(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        setup_fn(root)
        return
    before_status = _git_output(root, "status", "--porcelain").stdout
    gitignore = root / ".gitignore"
    gitignore_existed = gitignore.exists()
    gitignore_bytes = gitignore.read_bytes() if gitignore_existed else b""
    _ensure_local_graph_excludes(root)
    error: Exception | None = None
    try:
        setup_fn(root)
    except Exception as exc:  # restore metadata before preserving the original setup failure
        error = exc
    finally:
        if gitignore_existed:
            gitignore.write_bytes(gitignore_bytes)
        else:
            gitignore.unlink(missing_ok=True)
    after = _git_output(root, "status", "--porcelain")
    if after.returncode != 0 or after.stdout != before_status:
        detail = after.stderr.strip() or after.stdout.strip()
        raise CLIError(f"graph setup contaminated the candidate worktree: {detail}") from error
    if error is not None:
        raise error


def setup_graph(*, repo_root: Path | str) -> None:
    run_source_neutral_graph_setup(
        repo_root,
        lambda root: _run(["setup-graph", "--fix", "--repo-root", str(root)]),
    )


def graph_node_counts(*, repo_root: Path | str) -> dict:
    """`pebra graph-stats --json` → {total, callable, csharp_callable}. Used by the A/B graph preflight
    for an independent graph-validity check (a 'fresh' index that indexed no nodes is a real failure)."""
    return _run_json(["graph-stats", "--json", "--repo-root", str(repo_root)])


def capabilities(*, repo_root: Path | str) -> dict:
    """`pebra capabilities --json` → measured per-language capability tiers for the indexed repo."""
    return _run_json(["capabilities", "--json", "--repo-root", str(repo_root)])


def dependents(target: str, *, repo_root: Path | str) -> list[str]:
    """`pebra dependents --json` → the list of files that depend on ``target`` (file-level blast radius).
    Backs the blast_radius positive-control advisory. Empty list when the graph is absent."""
    result = dependents_result(target, repo_root=repo_root)
    files = result.get("dependent_files", [])
    return list(files) if isinstance(files, list) else []


def dependents_result(
    target: str, *, repo_root: Path | str, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> dict:
    """Structured `pebra dependents --json` payload, including graph availability metadata."""
    return _run_json(
        ["dependents", "--target", str(target), "--repo-root", str(repo_root), "--json"],
        timeout=timeout,
    )


def dashboard_proc(
    *, repo_root: Path | str, db: Path | str, port: int = 0, auth: str | None = None
) -> subprocess.Popen:
    """Start ``pebra dashboard`` as a long-running process. The caller reads stdout for the URL line and
    is responsible for teardown (see dashboard_harness). ``auth`` forwards ``--auth`` (e.g. "token")."""
    cmd = [
        _python(), "-u", "-m", "pebra", "dashboard", "--repo-root", str(repo_root), "--db", str(db),
        "--port", str(port),
    ]
    if auth is not None:
        cmd += ["--auth", auth]
    # PYTHONUNBUFFERED + -u: the URL line is print()ed before uvicorn.run() blocks, so it must flush
    # immediately or the reader never sees it (a pipe is block-buffered, unlike a tty).
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
