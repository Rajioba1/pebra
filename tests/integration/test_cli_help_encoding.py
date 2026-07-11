"""The CLI help surface must render on Windows' common legacy console encoding."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("command", [
    None,
    "assess",
    "accept-risk",
    "agent-init",
    "verify",
    "record-outcome",
    "learn",
    "promote",
    "scorecard",
    "dashboard",
    "setup-graph",
    "doctor",
    "graph-stats",
    "capabilities",
    "gate-check",
    "gate-hook",
    "dependents",
])
def test_help_is_cp1252_safe(command: str | None) -> None:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    argv = [sys.executable, "-m", "pebra"]
    if command is not None:
        argv.append(command)
    argv.append("--help")
    result = subprocess.run(
        argv,
        capture_output=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"usage:" in result.stdout.lower()
