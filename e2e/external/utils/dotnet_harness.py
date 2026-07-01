"""Run a real `dotnet build` as the post-edit OUTCOME signal. Pure stdlib; no pebra import.

The compiler is ground truth: a build failure after a "scoped" edit is exactly the materialized risk
PEBRA assessed pre-edit. Gated on the SDK being present (skips honestly if absent).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DotNetBuildResult:
    available: bool
    ran: bool
    passed: bool
    exit_code: int | None
    error_summary: str
    duration_seconds: float


def dotnet_available() -> bool:
    return shutil.which("dotnet") is not None


def run_build(repo_root: Path | str, sln: str = "TemplateBlueprint.sln", *,
              timeout: int = 600) -> DotNetBuildResult:
    if not dotnet_available():
        return DotNetBuildResult(False, False, False, None, "dotnet SDK not found", 0.0)
    start = time.time()
    proc = subprocess.run(
        ["dotnet", "build", str(Path(repo_root) / sln), "--nologo", "-v", "q"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=timeout,
    )
    duration = time.time() - start
    # CS errors usually land on stdout, but some SDK/MSBuild configs forward them to stderr — scan both.
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = [ln.strip() for ln in output.splitlines() if "error CS" in ln]
    return DotNetBuildResult(
        available=True, ran=True, passed=proc.returncode == 0, exit_code=proc.returncode,
        error_summary="\n".join(errors[:3]), duration_seconds=round(duration, 2),
    )
