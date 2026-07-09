"""v2 spawn-and-redirect: DashboardRegistry spawns the REAL pebra dashboard for a DISCOVERED clone
(never a client-supplied path), captures its bound URL, is idempotent per clone, fails closed on a
missing store / no-bind, and tears every child down on shutdown_all(). Popen is stubbed — no real spawn.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from e2e.experiments.agent_ab.runners.observatory import launch as launch_mod


class _FakeProc:
    def __init__(self, lines, pid=4242):
        self.stdout = iter(lines)
        self.pid = pid
        self._alive = True
        self.terminated = False
        self.killed = False
        self.waits = 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self.waits += 1
        self._alive = False
        return 0


class _StubbornProc(_FakeProc):
    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waits += 1
        if not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        self._alive = False
        return 0


def _stub_copy(monkeypatch):
    # The launch-LOGIC tests stub the file-copy (returning the source db path unchanged) so they don't
    # need real db files; _copy_db_to_temp itself is covered by test_copy_db_to_temp_leaves_clone_untouched.
    monkeypatch.setattr(launch_mod, "_copy_db_to_temp", lambda db: ("/fake/obs-tmp", db))


def _patch(monkeypatch, *, stores, proc, calls):
    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: stores)
    _stub_copy(monkeypatch)
    def _popen(cmd, **kwargs):
        calls.append(cmd)
        return proc
    monkeypatch.setattr(launch_mod.subprocess, "Popen", _popen)


def test_launch_spawns_and_returns_bound_url(tmp_path, monkeypatch):
    stores = [{"clone": "T1_seed0_abc", "db": "/x/db", "repo": "/x/repo"}]
    proc = _FakeProc(["booting...\n", "PEBRA Risk Observatory: http://127.0.0.1:5555/\n"])
    calls = []
    _patch(monkeypatch, stores=stores, proc=proc, calls=calls)
    reg = launch_mod.DashboardRegistry()
    res = reg.launch("r1", "T1_seed0_abc", ab_out=tmp_path, bind_timeout=5)
    assert res["status"] == "launched"
    assert res["url"] == "http://127.0.0.1:5555/"
    assert res["pid"] == 4242
    cmd = calls[0]
    assert cmd[:3] == [launch_mod.sys.executable, "-u", "-c"]
    assert "pebra.dashboard.server" in cmd[3]
    assert "read_only=True" in cmd[3]                  # serves the copy read-only
    assert "dashboard" not in cmd[4:]                  # bypasses `pebra dashboard`, which inits .pebra
    assert "/x/db" in cmd                              # the (stub) copied db path
    assert launch_mod.repo_id_for("/x/repo") in cmd    # the clone's repo_id is passed...
    assert "/x/repo" in cmd                            # ...and repo_root is passed for graph routes


def test_launch_is_idempotent_per_clone(tmp_path, monkeypatch):
    stores = [{"clone": "c", "db": "/db", "repo": "/repo"}]
    proc = _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"])
    calls = []
    _patch(monkeypatch, stores=stores, proc=proc, calls=calls)
    reg = launch_mod.DashboardRegistry()
    reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)
    res2 = reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)
    assert res2["status"] == "already_running"
    assert res2["url"] == "http://127.0.0.1:5555/"
    assert len(calls) == 1  # the second click reuses the running dashboard, does not respawn


def test_same_clone_name_in_different_runs_spawns_distinct_dashboards(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: [{"clone": "same", "db": f"/{run_id}/db",
                                                 "repo": f"/{run_id}/repo"}])
    _stub_copy(monkeypatch)
    procs = [
        _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"], pid=1),
        _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5556/\n"], pid=2),
    ]
    calls = []
    monkeypatch.setattr(launch_mod.subprocess, "Popen",
                        lambda cmd, **kwargs: (calls.append(cmd) or procs.pop(0)))
    reg = launch_mod.DashboardRegistry()

    r1 = reg.launch("r1", "same", ab_out=tmp_path, bind_timeout=5)
    r2 = reg.launch("r2", "same", ab_out=tmp_path, bind_timeout=5)

    assert r1["url"] == "http://127.0.0.1:5555/"
    assert r2["url"] == "http://127.0.0.1:5556/"
    assert len(calls) == 2


def test_unknown_clone_is_error_not_spawn(tmp_path, monkeypatch):
    calls = []
    _patch(monkeypatch, stores=[], proc=_FakeProc([]), calls=calls)
    reg = launch_mod.DashboardRegistry()
    res = reg.launch("r1", "ghost", ab_out=tmp_path, bind_timeout=5)
    assert res["status"] == "error"
    assert calls == []  # never spawned for a non-discovered clone


def test_store_without_repo_is_error(tmp_path, monkeypatch):
    _patch(monkeypatch, stores=[{"clone": "c", "db": "/db", "repo": None}],
           proc=_FakeProc([]), calls=[])
    reg = launch_mod.DashboardRegistry()
    assert reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)["status"] == "error"


def test_no_bound_url_terminates_and_errors(tmp_path, monkeypatch):
    stores = [{"clone": "c", "db": "/db", "repo": "/repo"}]
    proc = _FakeProc(["some noise, no url line\n"])
    _patch(monkeypatch, stores=stores, proc=proc, calls=[])
    reg = launch_mod.DashboardRegistry()
    res = reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=1)
    assert res["status"] == "error"
    assert proc.terminated is True  # the un-bound child is killed, not leaked
    assert proc.waits >= 1


def test_no_bound_url_kills_stubborn_child(tmp_path, monkeypatch):
    stores = [{"clone": "c", "db": "/db", "repo": "/repo"}]
    proc = _StubbornProc(["some noise, no url line\n"])
    _patch(monkeypatch, stores=stores, proc=proc, calls=[])
    reg = launch_mod.DashboardRegistry()
    res = reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=1)
    assert res["status"] == "error"
    assert proc.terminated is True
    assert proc.killed is True


def test_popen_failure_cleans_temp_copy(tmp_path, monkeypatch):
    stores = [{"clone": "c", "db": "/db", "repo": "/repo"}]
    tmp_dir = tmp_path / "obs-tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: stores)
    monkeypatch.setattr(launch_mod, "_copy_db_to_temp",
                        lambda db: (str(tmp_dir), str(tmp_dir / "pebra.db")))

    def _raise(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(launch_mod.subprocess, "Popen", _raise)
    res = launch_mod.DashboardRegistry().launch("r1", "c", ab_out=tmp_path, bind_timeout=1)
    assert res["status"] == "error"
    assert "spawn failed" in res["reason"]
    assert not tmp_dir.exists()


def test_launch_after_shutdown_is_refused_without_spawning(tmp_path, monkeypatch):
    calls = []
    _patch(monkeypatch, stores=[{"clone": "c", "db": "/db", "repo": "/repo"}],
           proc=_FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"]), calls=calls)
    reg = launch_mod.DashboardRegistry()
    reg.shutdown_all()
    res = reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)
    assert res["status"] == "error"
    assert calls == []  # nothing spawned after shutdown


def test_concurrent_same_clone_launch_does_not_double_spawn(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: [{"clone": "c", "db": "/db", "repo": "/repo"}])
    _stub_copy(monkeypatch)
    gate = threading.Event()       # released to let the first launch's URL read complete
    in_read = threading.Event()    # signals the first launch is mid-read, holding the clone lock

    class _GatedStdout:
        def __init__(self):
            self._done = False

        def __iter__(self):
            return self

        def __next__(self):
            if self._done:
                raise StopIteration
            in_read.set()
            gate.wait(5)
            self._done = True
            return "PEBRA Risk Observatory: http://127.0.0.1:5555/\n"

    proc = _FakeProc([])
    proc.stdout = _GatedStdout()
    calls = []
    monkeypatch.setattr(launch_mod.subprocess, "Popen",
                        lambda cmd, **kwargs: (calls.append(cmd) or proc))
    reg = launch_mod.DashboardRegistry()

    results: dict[str, dict] = {}

    def _launch(key):
        results[key] = reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)

    ta = threading.Thread(target=_launch, args=("a",))
    ta.start()
    assert in_read.wait(5)  # A is now blocked reading the URL, holding the per-clone lock
    tb = threading.Thread(target=_launch, args=("b",))
    tb.start()
    gate.set()  # let A finish; B must have been serialized behind the clone lock
    ta.join(5)
    tb.join(5)

    assert len(calls) == 1  # exactly ONE dashboard spawned for the clone
    assert sorted([results["a"]["status"], results["b"]["status"]]) == ["already_running", "launched"]


def test_copy_db_to_temp_leaves_clone_untouched(tmp_path):
    # The db (+ WAL sidecars) is file-copied without opening the clone db, so no reader-created
    # -wal/-shm appear in the clone dir, and the copied snapshot is readable by SQLite.
    clone = tmp_path / "JS1_seed0_x"
    clone.mkdir()
    db = clone / "pebra.db"
    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t(v TEXT)")
        con.execute("INSERT INTO t(v) VALUES ('ok')")
        assert (clone / "pebra.db-wal").exists()
        assert (clone / "pebra.db-shm").exists()
        before = {p.name for p in clone.iterdir()}

        tmp_dir, tmp_db = launch_mod._copy_db_to_temp(str(db))
        try:
            copied = Path(tmp_db)
            assert copied.read_bytes()
            assert (Path(tmp_dir) / "pebra.db-wal").exists()
            assert (Path(tmp_dir) / "pebra.db-shm").exists()
            ro = sqlite3.connect(copied.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                assert ro.execute("SELECT v FROM t").fetchone()[0] == "ok"
                assert ro.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                ro.close()
            assert {p.name for p in clone.iterdir()} == before  # clone dir NOT written to
        finally:
            launch_mod._rmtree(tmp_dir)
    finally:
        con.close()


def test_copy_db_to_temp_cleans_up_on_copy_failure(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    db = clone / "pebra.db"
    db.write_bytes(b"not copied")
    tmp_dir = tmp_path / "copy-target"

    def _mkdtemp(prefix):
        tmp_dir.mkdir()
        return str(tmp_dir)

    monkeypatch.setattr(launch_mod.tempfile, "mkdtemp", _mkdtemp)

    def _raise(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(launch_mod.shutil, "copy2", _raise)

    try:
        launch_mod._copy_db_to_temp(str(db))
        raise AssertionError("expected copy failure")
    except OSError as exc:
        assert "copy failed" in str(exc)
    assert not tmp_dir.exists()


def test_launch_serves_temp_copy_not_the_clone_db(tmp_path, monkeypatch):
    # At the launch level: the served db is a temp COPY (not the clone's pebra.db), the clone
    # dir is never written, and the temp copy is removed on shutdown. Real _copy_db_to_temp (not stubbed).
    clone = tmp_path / "JS1_seed0_x"
    clone.mkdir()
    db = clone / "pebra.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(v TEXT)")
    con.execute("INSERT INTO t(v) VALUES ('data')")
    con.commit()
    con.close()
    stores = [{"clone": clone.name, "db": str(db), "repo": str(tmp_path / "repo")}]
    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs", lambda run_id, ab_out: stores)
    proc = _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"])
    calls = []
    monkeypatch.setattr(launch_mod.subprocess, "Popen", lambda cmd, **kw: (calls.append(cmd) or proc))
    before = {p.name for p in clone.iterdir()}

    reg = launch_mod.DashboardRegistry()
    res = reg.launch("r1", clone.name, ab_out=tmp_path, bind_timeout=5)
    assert res["status"] == "launched"
    served_db = Path(calls[0][4])                       # cmd = [exe,-u,-c,CODE, <db>, <repo_id>]
    ro = sqlite3.connect(served_db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        assert ro.execute("SELECT v FROM t").fetchone()[0] == "data"
    finally:
        ro.close()
    assert clone not in served_db.parents               # served from temp, NOT the clone dir
    assert {p.name for p in clone.iterdir()} == before  # clone dir untouched (no -wal/-shm added)

    reg.shutdown_all()
    assert not served_db.exists()                       # temp copy reaped on shutdown


def test_relaunch_after_dead_child_reaps_old_temp_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: [{"clone": "c", "db": "/db", "repo": "/repo"}])
    tmp1 = tmp_path / "tmp1"
    tmp2 = tmp_path / "tmp2"
    tmp1.mkdir()
    tmp2.mkdir()
    copies = [(str(tmp1), str(tmp1 / "pebra.db")), (str(tmp2), str(tmp2 / "pebra.db"))]
    monkeypatch.setattr(launch_mod, "_copy_db_to_temp", lambda db: copies.pop(0))
    first = _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"], pid=1)
    second = _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5556/\n"], pid=2)
    procs = [first, second]
    monkeypatch.setattr(launch_mod.subprocess, "Popen", lambda cmd, **kwargs: procs.pop(0))
    reg = launch_mod.DashboardRegistry()

    assert reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)["pid"] == 1
    first._alive = False
    assert reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)["pid"] == 2

    assert not tmp1.exists()
    assert tmp2.exists()


def test_shutdown_all_terminates_children(tmp_path, monkeypatch):
    stores = [{"clone": "c", "db": "/db", "repo": "/repo"}]
    proc = _FakeProc(["PEBRA Risk Observatory: http://127.0.0.1:5555/\n"])
    _patch(monkeypatch, stores=stores, proc=proc, calls=[])
    reg = launch_mod.DashboardRegistry()
    reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)
    reg.shutdown_all()
    assert proc.terminated is True
    assert proc.waits >= 1


def test_shutdown_all_terminates_inflight_child(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(launch_mod.launch_dashboard, "list_run_dbs",
                        lambda run_id, ab_out: [{"clone": "c", "db": "/db", "repo": "/repo"}])
    _stub_copy(monkeypatch)
    in_read = threading.Event()
    release = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            return self

        def __next__(self):
            in_read.set()
            release.wait(5)
            raise StopIteration

    proc = _FakeProc([])
    proc.stdout = _BlockingStdout()
    monkeypatch.setattr(launch_mod.subprocess, "Popen", lambda cmd, **kwargs: proc)
    reg = launch_mod.DashboardRegistry()
    result: dict[str, dict] = {}

    thread = threading.Thread(target=lambda: result.setdefault(
        "launch", reg.launch("r1", "c", ab_out=tmp_path, bind_timeout=5)))
    thread.start()
    assert in_read.wait(5)
    reg.shutdown_all()
    release.set()
    thread.join(5)

    assert proc.terminated is True
    assert proc.waits >= 1
    assert result["launch"]["status"] == "error"
