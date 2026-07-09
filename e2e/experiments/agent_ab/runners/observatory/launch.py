"""v2 spawn-and-redirect: launch the REAL pebra dashboard for a discovered arm store and capture its
bound URL. Boundary-safe — it SHELLS a child Python process (the parent never imports pebra).

Read-only / never-writes-the-clone posture:
  * The clone's ``pebra.db`` is FILE-COPIED into a throwaway temp dir and the dashboard is served against
    the COPY. The clone is never opened by us — even a read-only SQLite open of a WAL db would create
    ``-wal``/``-shm`` sidecars in the clone that a read-only connection cannot remove. The copied db is
    validated before launch; if a live writer changes the source mid-copy and validation fails, launch
    fails closed instead of serving a broken snapshot.
  * The child serves the copy with ``read_only=True`` (SQLite ``mode=ro``: no schema/data writes) and
    is passed the CLONE's ``repo_id`` (the data is keyed by it) plus the clone's repo_root for graph reads
    — NOT via ``--repo-root`` CLI resolution (which would init ``.pebra/`` in the clone). ``repo_id_for``
    is pinned to production by a parity test.

Lifecycle:
  * Only ever launches for a clone DISCOVERED via ``launch_dashboard.list_run_dbs`` — repo/db come from
    that lookup, never from the caller.
  * OS-assigned free port (``--port 0``) so multiple arms run without collision; idempotent per (run,
    clone); ``shutdown_all()`` terminates every child AND removes its temp copy.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from e2e.experiments.agent_ab.runners import launch_dashboard
from e2e.experiments.agent_ab.runners.launch_dashboard import repo_id_for

# The real dashboard prints "PEBRA Risk Observatory: <url>" once bound (pebra/dashboard/server.py).
_URL_RE = re.compile(r"PEBRA Risk Observatory:\s*(\S+)")
_BIND_TIMEOUT_S = 20.0
_TERM_TIMEOUT_S = 2.0
# Serve the TEMP COPY read-only, keyed by the clone's repo_id. repo_root is passed only as graph context;
# it is not resolved by RepositoryRegistry and does not initialize .pebra/.
_DASHBOARD_SERVER_CODE = (
    "import sys\n"
    "from pebra.dashboard.server import serve\n"
    "serve(sys.argv[1], host='127.0.0.1', requested_port=0, token=None, "
    "repo_id=sys.argv[2], repo_root=sys.argv[3], read_only=True)\n"
)


def _validate_temp_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("PRAGMA quick_check").fetchone()
    finally:
        con.close()
    if not row or row[0] != "ok":
        raise OSError(f"copied dashboard db failed quick_check: {row[0] if row else 'no result'}")


def _copy_db_to_temp(db_path: str) -> tuple[str, str]:
    """File-copy the clone's pebra.db (+ any -wal/-shm sidecars) into a fresh temp dir WITHOUT opening
    it, so the clone is never touched. The copy is validated before use; validation failures clean up the
    temp dir and surface as OSError."""
    tmp_dir = tempfile.mkdtemp(prefix="pebra-obs-")
    src = Path(db_path)
    dst = Path(tmp_dir) / src.name
    try:
        shutil.copy2(src, dst)
        for suffix in ("-wal", "-shm"):
            sidecar = src.with_name(src.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(tmp_dir) / sidecar.name)
        _validate_temp_db(dst)
        return tmp_dir, str(dst)
    except (OSError, sqlite3.Error):
        _rmtree(tmp_dir)
        raise


def _rmtree(tmp_dir: str | None) -> None:
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
        self._inflight: dict[subprocess.Popen, str] = {}  # proc -> temp_dir (for spawns not yet registered)
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
        # different clones still spawn in parallel. discover -> copy -> spawn -> register happens under
        # this per-clone lock; _shutdown is re-checked after the spawn so an in-flight launch can't leak
        # a child (or its temp copy) past shutdown_all().
        key = _launch_key(run_id, clone, Path(ab_out))
        with self._clone_lock(key):
            with self._lock:
                if self._shutdown:
                    return {"status": "error", "reason": "observatory is shutting down"}
                existing = self._by_key.get(key)
                if existing is not None and existing["proc"].poll() is None:
                    return {"status": "already_running", "url": existing["url"],
                            "pid": existing["pid"]}
                if existing is not None:
                    self._by_key.pop(key, None)
                    stale = existing
                else:
                    stale = None
            if stale is not None:
                _terminate_process(stale["proc"])
                _rmtree(stale.get("tmp_dir"))

            store = next((s for s in launch_dashboard.list_run_dbs(run_id, ab_out=Path(ab_out))
                          if s["clone"] == clone), None)
            if store is None or not store.get("repo"):
                return {"status": "error", "reason": "no such store (or no repo/ dir) for this run"}

            try:
                tmp_dir, tmp_db = _copy_db_to_temp(store["db"])
            except OSError as exc:
                return {"status": "error", "reason": f"could not snapshot db: {exc}"}

            cmd = [sys.executable, "-u", "-c", _DASHBOARD_SERVER_CODE, tmp_db,
                   repo_id_for(store["repo"]), store["repo"]]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
            except OSError as exc:
                _rmtree(tmp_dir)
                return {"status": "error", "reason": f"could not launch dashboard: {exc}"}
            with self._lock:
                if self._shutdown:
                    _terminate_process(proc)
                    _rmtree(tmp_dir)
                    return {"status": "error", "reason": "observatory is shutting down"}
                self._inflight[proc] = tmp_dir
            url = _read_url(proc, bind_timeout)
            if url is None:
                with self._lock:
                    self._inflight.pop(proc, None)
                _terminate_process(proc)
                _rmtree(tmp_dir)
                return {"status": "error",
                        "reason": f"pebra dashboard did not bind within {bind_timeout:g}s"}
            with self._lock:
                self._inflight.pop(proc, None)
                if self._shutdown:  # shutdown_all ran during our spawn — terminate + clean, don't register
                    _terminate_process(proc)
                    _rmtree(tmp_dir)
                    return {"status": "error", "reason": "observatory is shutting down"}
                _drain_async(proc)
                self._by_key[key] = {"proc": proc, "url": url, "pid": proc.pid, "tmp_dir": tmp_dir}
            return {"status": "launched", "url": url, "pid": proc.pid}

    def shutdown_all(self) -> None:
        with self._lock:
            self._shutdown = True
            entries = list(self._by_key.values())
            inflight = dict(self._inflight)
            self._by_key.clear()
            self._inflight.clear()
        procs = [e["proc"] for e in entries] + list(inflight)
        for proc in procs:
            _terminate_process(proc)  # release the temp-db file handles before removing the temp dirs
        for tmp_dir in [e.get("tmp_dir") for e in entries] + list(inflight.values()):
            _rmtree(tmp_dir)
