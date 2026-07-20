from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from pebra.adapters import bounded_process as bp
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


def test_run_bounded_passes_isolated_cwd_and_environment(tmp_path) -> None:
    env = dict(os.environ)
    env["PEBRA_BOUNDED_CHILD_MARKER"] = "isolated"
    try:
        result = run_bounded(
            [
                sys.executable,
                "-c",
                "import os; from pathlib import Path; "
                "print(Path.cwd()); print(os.environ['PEBRA_BOUNDED_CHILD_MARKER'])",
            ],
            timeout=5,
            stdout_limit=1_024,
            stderr_limit=1_024,
            cwd=str(tmp_path),
            env=env,
        )
    except TypeError:
        pytest.fail("run_bounded does not support isolated cwd/environment")

    assert result.error is None
    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(tmp_path), "isolated"]


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


def test_run_bounded_cancellation_kills_process_tree_promptly(tmp_path) -> None:
    started = tmp_path / "started.txt"
    survived = tmp_path / "survived.txt"
    cancel = threading.Event()
    outcome: dict[str, object] = {}

    def invoke() -> None:
        began = time.monotonic()
        outcome["result"] = run_bounded(
            [
                sys.executable,
                "-c",
                "import sys,time; from pathlib import Path; "
                "Path(sys.argv[1]).write_text('started'); "
                "time.sleep(2); Path(sys.argv[2]).write_text('survived'); time.sleep(30)",
                str(started),
                str(survived),
            ],
            timeout=60,
            stdout_limit=100,
            stderr_limit=100,
            cancel_event=cancel,
        )
        outcome["elapsed"] = time.monotonic() - began

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    deadline = time.monotonic() + 3
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.exists() is True

    cancel.set()
    worker.join(2)

    assert worker.is_alive() is False
    result = outcome["result"]
    assert outcome["elapsed"] < 2
    assert result.error == "cancelled"
    time.sleep(0.1)
    assert survived.exists() is False


def test_run_bounded_launch_error_never_exposes_raw_path() -> None:
    result = run_bounded(
        [r"Z:\secret\does-not-exist.exe", "private-query"],
        timeout=1,
        stdout_limit=100,
        stderr_limit=100,
    )

    assert result.error == "launch_failed"
    assert "secret" not in result.stdout + result.stderr


def test_run_bounded_times_out_descendant_that_keeps_parent_pipes_open(tmp_path) -> None:
    started = tmp_path / "descendant-started.txt"
    parent_exited = tmp_path / "parent-exited.txt"
    survived = tmp_path / "descendant-survived.txt"
    child_code = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(1.5); Path(sys.argv[2]).write_text('survived', encoding='utf-8'); "
        "time.sleep(3)"
    )
    parent_code = "\n".join(
        [
            "import subprocess,sys,time",
            "from pathlib import Path",
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}, sys.argv[1], sys.argv[2]])",
            "deadline=time.monotonic()+2",
            "started=Path(sys.argv[1])",
            "while not started.exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "Path(sys.argv[3]).write_text('exited', encoding='utf-8')",
        ]
    )
    outcome: dict[str, object] = {}

    def invoke() -> None:
        began = time.monotonic()
        outcome["result"] = run_bounded(
            [
                sys.executable, "-c", parent_code, str(started), str(survived), str(parent_exited),
            ],
            timeout=1.0,
            stdout_limit=100,
            stderr_limit=100,
        )
        outcome["elapsed"] = time.monotonic() - began

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(1.75)
    exceeded_bound = worker.is_alive()

    assert exceeded_bound is False
    result = outcome["result"]
    assert outcome["elapsed"] < 1.75
    assert result.error == "timeout"
    assert started.exists() is True
    assert parent_exited.exists() is True
    time.sleep(0.6)
    assert survived.exists() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment")
@pytest.mark.parametrize("failure", ["job-unavailable", "job-error", "assignment-rejected"])
def test_run_bounded_never_resumes_process_without_job_containment(
    tmp_path, monkeypatch, failure
) -> None:
    marker = tmp_path / "command-ran.txt"
    if failure == "job-unavailable":
        monkeypatch.setattr(bp, "_windows_job", lambda: None)
    elif failure == "job-error":
        def unavailable():
            raise OSError(r"private C:\job-policy")

        monkeypatch.setattr(bp, "_windows_job", unavailable)
    else:
        monkeypatch.setattr(bp, "_assign_windows_job", lambda _job, _process: False)

    began = time.monotonic()
    result = run_bounded(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
            str(marker),
        ],
        timeout=2,
        stdout_limit=100,
        stderr_limit=100,
    )
    elapsed = time.monotonic() - began

    assert elapsed < 1.0
    assert result.error == "launch_failed"
    assert result.stdout == ""
    assert result.stderr == ""
    assert marker.exists() is False
    assert "private" not in result.stdout + result.stderr
