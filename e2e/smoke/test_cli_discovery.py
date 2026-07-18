from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pebra", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_root_help_and_version_are_discoverable_over_real_cli():
    help_result = _run("--help")
    assert help_result.returncode == 0
    assert "--version" in help_result.stdout
    assert "-V" in help_result.stdout

    for flag in ("--version", "-V"):
        version_result = _run(flag)
        assert version_result.returncode == 0
        assert version_result.stdout.startswith("PEBRA ")
        assert ("editable" in version_result.stdout) or ("installed" in version_result.stdout)
