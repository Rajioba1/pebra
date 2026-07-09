"""Run a real JS/TS build + test as the post-edit OUTCOME signal. Pure stdlib; no pebra import.

The sibling of ``dotnet_harness``: the TypeScript compiler / bundler is ground truth — a build failure
after a "scoped" edit is exactly the materialized risk PEBRA assessed pre-edit. The build/test commands
are FIXED PROFILES keyed by the detected package manager (never caller-supplied), so nothing in the
corpus JSON can become arbitrary shell execution.

Determinism guards: a lockfile is required (a missing lockfile fails closed — a floating install is a
validity risk), installs are frozen, and Nx's cache/daemon are disabled so a stale artifact from another
clone can never fabricate a pass. Windows-safe: binaries resolved via ``shutil.which`` (``pnpm.cmd``),
fixed argv, ``shell=False``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Lockfile -> package manager. Ordered: pnpm preferred (content-addressed store = cheap re-clone installs).
_LOCKFILES = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"))
_TS_ERROR = re.compile(r"error TS\d+")

RunnerFn = Callable[..., Any]


@dataclass
class NodeBuildResult:
    available: bool
    ran: bool
    passed: bool
    exit_code: int | None
    error_summary: str
    duration_seconds: float


@dataclass
class NodeTestResult:
    available: bool
    ran: bool
    passed: bool
    exit_code: int | None
    error_summary: str
    duration_seconds: float
    tests_selected: int | None = None


def node_available() -> bool:
    return shutil.which("node") is not None


def detect_package_manager(repo_root: Path | str) -> str | None:
    """The package manager implied by exactly one committed lockfile, or None (ambiguous/absent)."""
    root = Path(repo_root)
    matches = [pm for lockfile, pm in _LOCKFILES if (root / lockfile).is_file()]
    return matches[0] if len(matches) == 1 else None


def _lockfile_error(repo_root: Path | str) -> str:
    root = Path(repo_root)
    matches = [lockfile for lockfile, _pm in _LOCKFILES if (root / lockfile).is_file()]
    if len(matches) > 1:
        return f"ambiguous lockfiles: {', '.join(matches)}"
    return "no lockfile (refusing a nondeterministic install)"


def _node_version_pin_error(repo_root: Path | str) -> str | None:
    root = Path(repo_root)
    for marker in (".nvmrc", ".node-version"):
        if (root / marker).is_file() and (root / marker).read_text(encoding="utf-8").strip():
            return None
    package_json = root / "package.json"
    if not package_json.is_file():
        return "no package.json with engines.node (refusing an unpinned Node version)"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "package.json is not valid JSON"
    engines = data.get("engines") if isinstance(data, dict) else None
    node = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(node, str) and node.strip():
        return None
    return "no Node version pin (.nvmrc, .node-version, or package.json engines.node)"


def _node_env() -> dict[str, str]:
    # CI=1 suppresses interactive prompts; the rest silence funding/audit banners and disable Nx's daemon
    # + cache so a cached build/test artifact from another isolated clone can never fabricate a pass.
    return {
        **os.environ,
        "CI": "1",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_AUDIT": "false",
        "ADBLOCK": "1",
        "DISABLE_OPENCOLLECTIVE": "1",
        "NX_DAEMON": "false",
        "NX_SKIP_NX_CACHE": "true",
    }


def _install_argv(pm: str) -> list[str]:
    return {
        "pnpm": ["pnpm", "install", "--frozen-lockfile"],
        "yarn": ["yarn", "install", "--immutable"],
        "npm": ["npm", "ci"],
    }[pm]


def _build_argv(pm: str) -> list[str]:
    # FIXED profile: the specimen's own "build" script (tsc/bundler). Not a caller-supplied command.
    return {"pnpm": ["pnpm", "run", "build"], "yarn": ["yarn", "run", "build"], "npm": ["npm", "run", "build"]}[pm]


def _test_argv(pm: str, *, test_path: str | None = None, test_filter: str | None = None) -> list[str]:
    # FIXED profile: Vitest with the JSON reporter (structured pass/fail), optional file + ``-t`` filter.
    base = {"pnpm": ["pnpm", "exec", "vitest", "run"], "yarn": ["yarn", "vitest", "run"], "npm": ["npx", "vitest", "run"]}[pm]
    argv = list(base)
    if test_path:
        argv.append(str(test_path))
    argv.append("--reporter=json")
    if test_filter:
        argv += ["-t", test_filter]
    return argv


def _scan_build_errors(output: str) -> list[str]:
    lines = [
        ln.strip()
        for ln in output.splitlines()
        if _TS_ERROR.search(ln) or ln.lstrip().startswith(("ERROR", "✘"))
    ]
    return lines[:20]


def _build_error_summary(output: str) -> str:
    scanned = _scan_build_errors(output)
    if scanned:
        return "\n".join(scanned)
    fallback = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return "\n".join(fallback[:20])


def _parse_vitest(stdout: str) -> tuple[int | None, int | None]:
    """(numTotalTests, numFailedTests) from Vitest ``--reporter=json`` stdout, tolerating leading noise."""
    if not stdout:
        return None, None
    candidates = [stdout]
    start, end = stdout.find("{"), stdout.rfind("}")
    if start != -1 and end > start:
        candidates.append(stdout[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "numTotalTests" in data:
            return data.get("numTotalTests"), data.get("numFailedTests")
    return None, None


def _resolve_argv(argv: list[str]) -> list[str]:
    """Resolve argv[0] to its real path (``pnpm`` -> ``pnpm.cmd`` on Windows); keeps shell=False safe."""
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise FileNotFoundError(argv[0])
    return [resolved, *argv[1:]]


def _default_runner(argv: list[str], *, cwd: str, timeout: int, env: dict[str, str]):
    return subprocess.run(
        _resolve_argv(argv), cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
    )


def _ensure_installed(root: Path, pm: str, runner: RunnerFn, timeout: int) -> Any | None:
    """Frozen install if node_modules is absent. Returns the failed proc, or None on success/skip."""
    if (root / "node_modules").is_dir():
        return None
    proc = _run_fixed(runner, _install_argv(pm), root=root, timeout=timeout)
    return proc if proc.returncode != 0 else None


def _run_fixed(runner: RunnerFn, argv: list[str], *, root: Path, timeout: int) -> Any:
    """Run a fixed profile command; normalize missing executables to a CompletedProcess-like object."""
    try:
        return runner(argv, cwd=str(root), timeout=timeout, env=_node_env())
    except FileNotFoundError as exc:
        missing = Path(str(exc)).name or str(exc)
        return subprocess.CompletedProcess(argv, 127, "", f"executable not found: {missing}")


def run_build(
    repo_root: Path | str, *, timeout: int = 600, install_timeout: int = 900, runner: RunnerFn | None = None,
) -> NodeBuildResult:
    runner = runner or _default_runner
    if not node_available():
        return NodeBuildResult(False, False, False, None, "node not found", 0.0)
    pm = detect_package_manager(repo_root)
    if pm is None:
        return NodeBuildResult(
            False, False, False, None, _lockfile_error(repo_root), 0.0
        )
    root = Path(repo_root).resolve()
    pin_error = _node_version_pin_error(root)
    if pin_error is not None:
        return NodeBuildResult(False, False, False, None, pin_error, 0.0)
    start = time.time()
    failed_install = _ensure_installed(root, pm, runner, install_timeout)
    if failed_install is not None:
        return NodeBuildResult(
            True, False, False, failed_install.returncode, "dependency install failed",
            round(time.time() - start, 2),
        )
    proc = _run_fixed(runner, _build_argv(pm), root=root, timeout=timeout)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return NodeBuildResult(
        available=True, ran=True, passed=proc.returncode == 0, exit_code=proc.returncode,
        error_summary="" if proc.returncode == 0 else _build_error_summary(output),
        duration_seconds=round(time.time() - start, 2),
    )


def run_tests(
    repo_root: Path | str, *, test_path: Path | str | None = None, test_filter: str | None = None,
    timeout: int = 600, install_timeout: int = 900, runner: RunnerFn | None = None,
) -> NodeTestResult:
    """Run Vitest as the semantic-correctness oracle. A targeted run that selected 0 tests is NOT a pass
    (the fabricated-pass trap: a filter that matches nothing exits 0)."""
    runner = runner or _default_runner
    if not node_available():
        return NodeTestResult(False, False, False, None, "node not found", 0.0)
    pm = detect_package_manager(repo_root)
    if pm is None:
        return NodeTestResult(
            False, False, False, None, _lockfile_error(repo_root), 0.0
        )
    root = Path(repo_root).resolve()
    pin_error = _node_version_pin_error(root)
    if pin_error is not None:
        return NodeTestResult(False, False, False, None, pin_error, 0.0)
    start = time.time()
    failed_install = _ensure_installed(root, pm, runner, install_timeout)
    if failed_install is not None:
        return NodeTestResult(
            True, False, False, failed_install.returncode, "dependency install failed",
            round(time.time() - start, 2),
        )
    argv = _test_argv(pm, test_path=str(test_path) if test_path else None, test_filter=test_filter)
    proc = _run_fixed(runner, argv, root=root, timeout=timeout)
    total, failed = _parse_vitest(proc.stdout or "")
    targeted = test_path is not None or test_filter is not None
    if targeted:
        passed = proc.returncode == 0 and isinstance(total, int) and total > 0 and failed == 0
    else:
        passed = proc.returncode == 0 and (failed in (0, None))
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = [ln.strip() for ln in output.splitlines() if "fail" in ln.lower() or "error" in ln.lower()]
    return NodeTestResult(
        available=True, ran=True, passed=passed, exit_code=proc.returncode,
        error_summary="\n".join(errors[:15]), duration_seconds=round(time.time() - start, 2),
        tests_selected=total,
    )
