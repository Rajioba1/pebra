from __future__ import annotations

import sys

from pebra.adapters.bounded_process import run_bounded


def test_run_bounded_drains_but_caps_both_output_streams() -> None:
    result = run_bounded(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('o'*100000); sys.stderr.write('e'*100000)",
        ],
        timeout=10,
        stdout_limit=1_024,
        stderr_limit=512,
    )

    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= 1_024
    assert len(result.stderr.encode("utf-8")) <= 512
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.error is None


def test_run_bounded_kills_timed_out_process_with_stable_category() -> None:
    result = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.05,
        stdout_limit=100,
        stderr_limit=100,
    )

    assert result.error == "timeout"
    assert result.stdout == ""
    assert result.stderr == ""


def test_run_bounded_launch_error_never_exposes_raw_path() -> None:
    result = run_bounded(
        [r"Z:\secret\does-not-exist.exe", "private-query"],
        timeout=1,
        stdout_limit=100,
        stderr_limit=100,
    )

    assert result.error == "launch_failed"
    assert "secret" not in result.stdout + result.stderr
