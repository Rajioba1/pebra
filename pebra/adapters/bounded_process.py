"""Shell-free subprocess execution with bounded captured output."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from typing import BinaryIO, Literal


ProcessError = Literal["launch_failed", "timeout"]


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error: ProcessError | None


def _drain(stream: BinaryIO, limit: int, sink: bytearray, total: list[int]) -> None:
    while True:
        chunk = stream.read(8_192)
        if not chunk:
            return
        total[0] += len(chunk)
        remaining = limit - len(sink)
        if remaining > 0:
            sink.extend(chunk[:remaining])


def run_bounded(
    argv: list[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedProcessResult:
    """Drain both pipes to EOF while retaining no more than each explicit byte limit."""
    stdout_limit = max(0, stdout_limit)
    stderr_limit = max(0, stderr_limit)
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return BoundedProcessResult(None, "", "", False, False, "launch_failed")
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    stdout_total = [0]
    stderr_total = [0]
    threads = (
        threading.Thread(
            target=_drain, args=(process.stdout, stdout_limit, stdout, stdout_total), daemon=True
        ),
        threading.Thread(
            target=_drain, args=(process.stderr, stderr_limit, stderr, stderr_total), daemon=True
        ),
    )
    for thread in threads:
        thread.start()
    error: ProcessError | None = None
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        error = "timeout"
        process.kill()
        returncode = process.wait()
    for thread in threads:
        thread.join()
    return BoundedProcessResult(
        returncode,
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
        stdout_total[0] > stdout_limit,
        stderr_total[0] > stderr_limit,
        error,
    )
