"""Shell-free subprocess execution with bounded captured output."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import BinaryIO, Literal


ProcessError = Literal["launch_failed", "timeout"]
_TERMINATION_GRACE_SECONDS = 0.5
_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_TH32CS_SNAPPROCESS = 0x00000002


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


def _windows_process_parents() -> dict[int, int]:
    if os.name != "nt":
        return {}

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or int(snapshot) == ctypes.c_void_p(-1).value:
        return {}
    parents: dict[int, int] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        _close_windows_handle(int(snapshot))
    return parents


def _expand_windows_tree(known: set[int], parents: dict[int, int]) -> None:
    while True:
        children = {
            pid for pid, parent_pid in parents.items()
            if parent_pid in known and pid not in known
        }
        if not children:
            return
        known.update(children)


def _track_windows_tree(
    known: set[int], lock: threading.Lock, stop: threading.Event
) -> None:
    while not stop.is_set():
        try:
            parents = _windows_process_parents()
        except (AttributeError, OSError, ValueError):
            parents = {}
        with lock:
            _expand_windows_tree(known, parents)
        stop.wait(0.005)


def _terminate_windows_pid(pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        _close_windows_handle(int(handle))


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _join_until(threads: tuple[threading.Thread, ...], deadline: float) -> bool:
    for thread in threads:
        thread.join(_remaining(deadline))
    return all(not thread.is_alive() for thread in threads)


def _terminate_tree(
    process: subprocess.Popen[bytes],
    windows_job: int | None,
    deadline: float,
    tracked_pids: set[int] | None = None,
    tracker_lock: threading.Lock | None = None,
) -> int | None:
    if os.name == "nt":
        if windows_job is not None:
            _close_windows_handle(windows_job)
            windows_job = None
        else:
            try:
                parents = _windows_process_parents()
                if tracked_pids is None:
                    known = {process.pid}
                elif tracker_lock is None:
                    known = set(tracked_pids)
                else:
                    with tracker_lock:
                        known = set(tracked_pids)
                _expand_windows_tree(known, parents)
                targets = tuple(
                    sorted((pid for pid in known if pid in parents), reverse=True)
                )
            except (AttributeError, OSError, ValueError):
                targets = (process.pid,)
            for pid in targets:
                try:
                    _terminate_windows_pid(pid)
                except (AttributeError, OSError, ValueError):
                    pass
            for pid in targets:
                if _remaining(deadline) <= 0:
                    break
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=_remaining(deadline),
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
            if process.poll() is None:
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
) -> BoundedProcessResult:
    """Drain both pipes under one deadline while retaining explicit byte limits."""
    stdout_limit = max(0, stdout_limit)
    stderr_limit = max(0, stderr_limit)
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
            **popen_options,
        )
    except OSError:
        if windows_job is not None:
            _close_windows_handle(windows_job)
        return BoundedProcessResult(None, "", "", False, False, "launch_failed")
    tracked_pids: set[int] | None = None
    tracker_lock: threading.Lock | None = None
    tracker_stop: threading.Event | None = None
    tracker: threading.Thread | None = None
    if os.name == "nt":
        assigned = windows_job is not None and _assign_windows_job(windows_job, process)
        if not assigned:
            tracked_pids = {process.pid}
            tracker_lock = threading.Lock()
            tracker_stop = threading.Event()
            tracker = threading.Thread(
                target=_track_windows_tree,
                args=(tracked_pids, tracker_lock, tracker_stop),
                daemon=True,
            )
            tracker.start()
        resumed = _resume_windows_process(process)
        if windows_job is not None and not assigned:
            _close_windows_handle(windows_job)
            windows_job = None
        if not resumed:
            grace_deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
            _terminate_tree(
                process, windows_job, grace_deadline, tracked_pids, tracker_lock
            )
            if tracker_stop is not None:
                tracker_stop.set()
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
        returncode = process.wait(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        error = "timeout"
        returncode = None
    if error is None and not _join_until(threads, deadline):
        error = "timeout"
    if error == "timeout":
        grace_deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        returncode = _terminate_tree(
            process, windows_job, grace_deadline, tracked_pids, tracker_lock
        )
        windows_job = None
        _join_until(threads, grace_deadline)
    if tracker_stop is not None:
        tracker_stop.set()
    if tracker is not None:
        tracker.join(_remaining(deadline if error is None else grace_deadline))
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
