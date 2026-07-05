"""Run a real `dotnet build` as the post-edit OUTCOME signal. Pure stdlib; no pebra import.

The compiler is ground truth: a build failure after a "scoped" edit is exactly the materialized risk
PEBRA assessed pre-edit. Gated on the SDK being present (skips honestly if absent).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from e2e.external.utils import diagnostic_parser as dp


@dataclass
class DotNetBuildResult:
    available: bool
    ran: bool
    passed: bool
    exit_code: int | None
    error_summary: str
    duration_seconds: float
    # Phase 1 attribution (additive; default empty so all existing construction is unaffected):
    structured_diagnostics: list = field(default_factory=list)  # every parsed CS diagnostic
    delta_diagnostics: list = field(default_factory=list)  # only diagnostics NEW vs the baseline


def dotnet_available() -> bool:
    return shutil.which("dotnet") is not None


def run_build(repo_root: Path | str, sln: str = "TemplateBlueprint.sln", *,
              timeout: int = 600) -> DotNetBuildResult:
    if not dotnet_available():
        return DotNetBuildResult(False, False, False, None, "dotnet SDK not found", 0.0)
    root = Path(repo_root).resolve()
    start = time.time()
    proc = subprocess.run(
        ["dotnet", "build", str(root / sln), "--nologo", "-v", "q"],
        cwd=str(root), capture_output=True, text=True, timeout=timeout,
    )
    duration = time.time() - start
    # CS errors usually land on stdout, but some SDK/MSBuild configs forward them to stderr — scan both.
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = [ln.strip() for ln in output.splitlines() if "error CS" in ln]
    return DotNetBuildResult(
        available=True, ran=True, passed=proc.returncode == 0, exit_code=proc.returncode,
        error_summary="\n".join(errors[:20]), duration_seconds=round(duration, 2),
    )


@dataclass
class DotNetTestResult:
    available: bool
    ran: bool
    passed: bool
    exit_code: int | None
    error_summary: str
    duration_seconds: float
    tests_selected: int | None = None


def _selected_test_count(output: str) -> int | None:
    totals = [int(m.group(1)) for m in re.finditer(r"\bTotal(?: tests)?:\s*(\d+)", output)]
    return sum(totals) if totals else None


def run_tests(repo_root: Path | str, sln: str = "TemplateBlueprint.sln", *,
              project: Path | str | None = None, test_filter: str | None = None,
              timeout: int = 600) -> DotNetTestResult:
    """Run `dotnet test` as the semantic-correctness oracle. Skips honestly if the SDK is absent.

    Pass ``project`` (a .csproj path) to target a SPECIFIC test project directly. This is what the
    evaluator does: targeting the injected project avoids the fabricated-pass trap where
    ``dotnet test <solution>`` exits 0 ("no tests ran") when the test project isn't referenced by the
    solution. With no ``project`` it falls back to the solution.
    """
    if not dotnet_available():
        return DotNetTestResult(False, False, False, None, "dotnet SDK not found", 0.0)
    root = Path(repo_root).resolve()
    target = str(project) if project is not None else str(root / sln)
    args = ["dotnet", "test", target, "--nologo", "-v", "q"]
    if test_filter:
        args.extend(["--filter", test_filter])
    start = time.time()
    env = {**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en"}
    proc = subprocess.run(
        args,
        cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env,
    )
    duration = time.time() - start
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = [ln.strip() for ln in output.splitlines()
              if ("error" in ln.lower() or "failed" in ln.lower())]
    selected = _selected_test_count(output)
    targeted = project is not None or test_filter is not None
    passed = proc.returncode == 0 and not (targeted and selected == 0)
    return DotNetTestResult(
        available=True, ran=True, passed=passed, exit_code=proc.returncode,
        error_summary="\n".join(errors[:15]), duration_seconds=round(duration, 2),
        tests_selected=selected,
    )


def augment_with_diagnostics(output: str, repo_root: Path | str, baseline_keys):
    """Pure: parse combined build output into structured diagnostics and the edit-attributable delta."""
    structured = dp.parse_diagnostics(output, str(repo_root))
    delta = dp.compute_delta(structured, baseline_keys or frozenset())
    return structured, delta


def run_build_delta(repo_root: Path | str, sln: str = "TemplateBlueprint.sln", *,
                    baseline_keys=None, timeout: int = 600) -> DotNetBuildResult:
    """Like run_build, but also populates structured_diagnostics + delta_diagnostics (vs baseline_keys).

    Additive sibling of run_build — the compiler is run once here and the raw output parsed; run_build
    itself is untouched. Skips honestly (available=False) when the SDK is absent.
    """
    if not dotnet_available():
        return DotNetBuildResult(False, False, False, None, "dotnet SDK not found", 0.0)
    root = Path(repo_root).resolve()
    start = time.time()
    proc = subprocess.run(
        ["dotnet", "build", str(root / sln), "--nologo", "-v", "q"],
        cwd=str(root), capture_output=True, text=True, timeout=timeout,
    )
    duration = time.time() - start
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = [ln.strip() for ln in output.splitlines() if "error CS" in ln]
    structured, delta = augment_with_diagnostics(output, root, baseline_keys)
    return DotNetBuildResult(
        available=True, ran=True, passed=proc.returncode == 0, exit_code=proc.returncode,
        error_summary="\n".join(errors[:20]), duration_seconds=round(duration, 2),
        structured_diagnostics=structured, delta_diagnostics=delta,
    )
