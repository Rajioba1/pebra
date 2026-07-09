"""v2 spawn-and-redirect: launch the REAL pebra dashboard for a discovered arm store and capture its
bound URL. Boundary-safe — it SHELLS a child Python process (the parent never imports pebra).

Safety posture:
  * Only ever launches for a clone DISCOVERED via ``launch_dashboard.list_run_dbs`` — the repo/db paths
    come from that lookup, NEVER from the caller. A client can pass any ``clone`` string; a non-matching
    one simply resolves to no store and errors.
  * The spawned dashboard binds loopback with an OS-assigned free port (``--port 0``) so multiple arms
    can run at once without collision; its concrete URL is read back from its startup line.
  * Idempotent per clone (a re-launch returns the already-running URL); all children are terminated on
    ``shutdown_all()`` (called from the server's shutdown path).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
from pathlib import Path

from e2e.experiments.agent_ab.runners import launch_dashboard

# The real dashboard prints "PEBRA Risk Observatory: <url>" once bound (pebra/dashboard/server.py).
_URL_RE = re.compile(r"PEBRA Risk Observatory:\s*(\S+)")
_BIND_TIMEOUT_S = 20.0
_TERM_TIMEOUT_S = 2.0
_DASHBOARD_SERVER_CODE = (
    "import sys\n"
    "from pebra.dashboard.server import serve\n"
    "serve(sys.argv[1], host='127.0.0.1', requested_port=0, token=None, "
    "repo_id=sys.argv[2], repo_root=sys.argv[3])\n"
)


def _repo_id_for(repo_root: str) -> str:
    """Read-only twin of RepositoryRegistry's stable repo_id formula.

    `pebra dashboard --repo-root` initializes `.pebra`; v2 launch must not write into the assay clone.
    Passing the repo_id directly lets the child serve the real dashboard without touching repo files.
    """
    root = Path(repo_root).resolve()
    return "repo_" + hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]


def _launch_key(run_id: str, clone: str, ab_out: Path) -> tuple[str, str, str]:
    return (str(Path(ab_out).resolve()), run_id, clone)


def _terminate_process(proc: subprocess.Popen, timeout: float = _TERM_TIMEOUT_S) -> None:
    """Terminate, wait, then kill as needed so shutdown reaps children instead of only signalling."""
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass
    stdout = getattr(proc, "stdout", None)
    try:
        if stdout is not None:
            stdout.close()
    except (AttributeError, OSError, ValueError):
        pass


def _read_url(proc: subprocess.Popen, timeout: float) -> str | None:
    """Read the child's stdout until it prints its bound URL, or ``timeout`` elapses."""
    found: dict[str, str] = {}

    def _reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                match = _URL_RE.search(line)
                if match:
                    found["url"] = match.group(1)
                    return
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    return found.get("url")


def _drain_async(proc: subprocess.Popen) -> None:
    """Keep draining the child's stdout so its pipe buffer can't fill and block the dashboard."""
    def _drain() -> None:
        try:
            for _ in proc.stdout:  # type: ignore[union-attr]
                pass
        except (OSError, ValueError):
            pass

    threading.Thread(target=_drain, daemon=True).start()


class DashboardRegistry:
    """Tracks spawned pebra dashboards, one per (run, clone). Thread-safe."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], dict] = {}
        self._clone_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._inflight: set[subprocess.Popen] = set()
        self._lock = threading.Lock()   # guards _by_key / _clone_locks / _inflight / _shutdown
        self._shutdown = False

    def _clone_lock(self, key: tuple[str, str, str]) -> threading.Lock:
        with self._lock:
            lock = self._clone_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._clone_locks[key] = lock
            return lock

    def launch(self, run_id: str, clone: str, *, ab_out: Path,
               bind_timeout: float = _BIND_TIMEOUT_S) -> dict:
        # Serialize launches for the SAME clone (so a slow bind + a re-click can't double-spawn), while
        # different clones still spawn in parallel. discover -> spawn -> register happens under this
        # per-clone lock; _shutdown is re-checked after the spawn so an in-flight launch can't leak a
        # child past shutdown_all().
        key = _launch_key(run_id, clone, Path(ab_out))
        with self._clone_lock(key):
            with self._lock:
                if self._shutdown:
                    return {"status": "error", "reason": "observatory is shutting down"}
                existing = self._by_key.get(key)
                if existing is not None and existing["proc"].poll() is None:
                    return {"status": "already_running", "url": existing["url"],
                            "pid": existing["pid"]}

            store = next((s for s in launch_dashboard.list_run_dbs(run_id, ab_out=Path(ab_out))
                          if s["clone"] == clone), None)
            if store is None or not store.get("repo"):
                return {"status": "error", "reason": "no such store (or no repo/ dir) for this run"}

            cmd = [sys.executable, "-u", "-c", _DASHBOARD_SERVER_CODE, store["db"],
                   _repo_id_for(store["repo"]), store["repo"]]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            with self._lock:
                if self._shutdown:
                    _terminate_process(proc)
                    return {"status": "error", "reason": "observatory is shutting down"}
                self._inflight.add(proc)
            url = _read_url(proc, bind_timeout)
            if url is None:
                with self._lock:
                    self._inflight.discard(proc)
                _terminate_process(proc)
                return {"status": "error",
                        "reason": f"pebra dashboard did not bind within {bind_timeout:g}s"}
            with self._lock:
                self._inflight.discard(proc)
                if self._shutdown:  # shutdown_all ran during our spawn — terminate, don't register/leak
                    _terminate_process(proc)
                    return {"status": "error", "reason": "observatory is shutting down"}
                _drain_async(proc)
                self._by_key[key] = {"proc": proc, "url": url, "pid": proc.pid}
            return {"status": "launched", "url": url, "pid": proc.pid}

    def shutdown_all(self) -> None:
        with self._lock:
            self._shutdown = True
            procs = [entry["proc"] for entry in self._by_key.values()]
            procs.extend(self._inflight)
            self._by_key.clear()
            self._inflight.clear()
        for proc in procs:
            _terminate_process(proc)
