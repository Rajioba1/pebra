"""Shell-free subprocess execution with bounded captured output."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from ctypes import wintypes
from typing import BinaryIO, Literal


ProcessError = Literal["cancelled", "launch_failed", "timeout"]
_TERMINATION_GRACE_SECONDS = 0.5
_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error: ProcessError | None


def _drain(stream: BinaryIO, limit: int, sink: bytearray, total: list[int]) -> None:
    try:
        while True:
            chunk = stream.read(8_192)
            if not chunk:
                return
            total[0] += len(chunk)
            remaining = limit - len(sink)
            if remaining > 0:
                sink.extend(chunk[:remaining])
    except (OSError, ValueError):
        return


def _windows_job() -> int | None:
    if os.name != "nt":
        return None

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _close_windows_handle(int(handle))
        return None
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _assign_windows_job(handle: int, process: subprocess.Popen[bytes]) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    return bool(
        kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(handle), wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        )
    )


def _resume_windows_process(process: subprocess.Popen[bytes]) -> bool:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = wintypes.LONG
    return ntdll.NtResumeProcess(
        wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    ) == 0


def _abort_windows_launch(
    process: subprocess.Popen[bytes], windows_job: int | None
) -> None:
    """Terminate the exact still-suspended process and release its local handles."""
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    try:
        process.kill()
    except OSError:
        pass
    if windows_job is not None:
        _close_windows_handle(windows_job)
    try:
        process.wait(timeout=_remaining(deadline))
    except (OSError, subprocess.TimeoutExpired):
        pass
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _join_until(threads: tuple[threading.Thread, ...], deadline: float) -> bool:
    for thread in threads:
        thread.join(_remaining(deadline))
    return all(not thread.is_alive() for thread in threads)


def _terminate_tree(
    process: subprocess.Popen[bytes], windows_job: int | None, deadline: float
) -> int | None:
    if os.name == "nt":
        if windows_job is not None:
            _close_windows_handle(windows_job)
        elif process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        return process.wait(timeout=_remaining(deadline))
    except (OSError, subprocess.TimeoutExpired):
        return process.poll()


def run_bounded(
    argv: list[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> BoundedProcessResult:
    """Drain both pipes under one deadline while retaining explicit byte limits."""
    stdout_limit = max(0, stdout_limit)
    stderr_limit = max(0, stderr_limit)
    if cancel_event is not None and cancel_event.is_set():
        return BoundedProcessResult(None, "", "", False, False, "cancelled")
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        windows_job = _windows_job()
    except (AttributeError, OSError, ValueError):
        windows_job = None
    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = _CREATE_SUSPENDED
    elif os.name != "nt":
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            **popen_options,
        )
    except OSError:
        if windows_job is not None:
            _close_windows_handle(windows_job)
        return BoundedProcessResult(None, "", "", False, False, "launch_failed")
    if os.name == "nt":
        try:
            assigned = windows_job is not None and _assign_windows_job(windows_job, process)
        except (AttributeError, OSError, ValueError):
            assigned = False
        if not assigned:
            _abort_windows_launch(process, windows_job)
            return BoundedProcessResult(None, "", "", False, False, "launch_failed")
        try:
            resumed = _resume_windows_process(process)
        except (AttributeError, OSError, ValueError):
            resumed = False
        if not resumed:
            _abort_windows_launch(process, windows_job)
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
    returncode = process.poll()
    while returncode is None:
        if cancel_event is not None and cancel_event.is_set():
            error = "cancelled"
            break
        remaining = _remaining(deadline)
        if remaining <= 0:
            error = "timeout"
            break
        try:
            returncode = process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            returncode = None
    while error is None and any(thread.is_alive() for thread in threads):
        if cancel_event is not None and cancel_event.is_set():
            error = "cancelled"
            break
        remaining = _remaining(deadline)
        if remaining <= 0:
            error = "timeout"
            break
        for thread in threads:
            thread.join(min(0.05, remaining))
    if error is not None:
        grace_deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        returncode = _terminate_tree(process, windows_job, grace_deadline)
        windows_job = None
        _join_until(threads, grace_deadline)
    if windows_job is not None:
        _close_windows_handle(windows_job)
    for stream, thread in zip((process.stdout, process.stderr), threads, strict=True):
        if not thread.is_alive():
            stream.close()
    return BoundedProcessResult(
        returncode,
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
        stdout_total[0] > stdout_limit,
        stderr_total[0] > stderr_limit,
        error,
    )
