"""The observatory per-arm drilldown serves the REAL dashboard API with
read_only=True; hitting a read route must NOT mutate the served db (an experiment clone's pebra.db).

The observatory's own GET-route "never writes into a run dir" regression (in the e2e suite) cannot reach
this — it never spawns the product dashboard API. This test exercises `create_app(read_only=True)`
directly (import of pebra is allowed under tests/) and asserts the clone dir + db file are unchanged.
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.dashboard.server import create_app

TestClient = pytest.importorskip("starlette.testclient").TestClient
_HOSTS = ["testserver", "127.0.0.1", "localhost"]


def _seed(db) -> None:
    SqliteStore(str(db)).close()  # materialize the schema (read_only cannot)


def test_read_only_dashboard_api_never_modifies_the_db_file(tmp_path):
    # The read_only store's guarantee: the db FILE and all data are byte-identical after serving reads
    # (no schema/data writes). NOTE it does NOT guarantee zero sidecar files — a mode=ro reader of a
    # WAL db creates -wal/-shm it cannot remove (removal is a write). The observatory therefore serves a
    # TEMP COPY (see test_launch_serves_a_temp_copy_leaving_clone_untouched) so the real clone is never
    # opened; this test pins the narrower, production-wide ro-store guarantee.
    db = tmp_path / "pebra.db"
    _seed(db)
    before_db = db.read_bytes()

    app = create_app(str(db), None, repo_id="repo_x", repo_root=None, read_only=True,
                     allowed_hosts=_HOSTS)
    with TestClient(app) as client:
        assert client.get("/api/chain-status").status_code == 200

    assert db.read_bytes() == before_db  # db file/data untouched


def test_every_store_open_under_read_only_is_actually_read_only(tmp_path, monkeypatch):
    # Every route opens its own SqliteStore through _open. Under read_only=True EVERY store open must
    # be ro — else `pebra dashboard --read-only` opens read-write
    # (WAL sidecars + any schema migration) the first time an assessment detail is fetched. A file-mtime
    # check misses this on a current-schema db (the RW re-open is idempotent), so spy the opens directly.
    import pebra.dashboard.api as api_mod

    db = tmp_path / "pebra.db"
    _seed(db)
    seen: list[bool] = []
    real_store = api_mod.SqliteStore

    def _spy(path, **kwargs):
        seen.append(bool(kwargs.get("read_only", False)))
        return real_store(path, **kwargs)

    monkeypatch.setattr(api_mod, "SqliteStore", _spy)
    app = create_app(str(db), None, repo_id="repo_x", repo_root=None, read_only=True, allowed_hosts=_HOSTS)
    with TestClient(app) as client:
        assert client.get("/api/chain-status").status_code == 200
        assert client.get("/api/repos/repo_x/assessments/nope").status_code == 404

    assert seen  # both routes opened a store
    assert all(seen), "every store open under read_only=True must pass read_only=True"


def test_read_write_dashboard_api_would_write_the_db(tmp_path):
    # Control (not vacuous): the DEFAULT (read_only=False) path is exactly the one that writes — it
    # creates + schema-inits a missing db on first request. read_only never would.
    db = tmp_path / "pebra.db"
    app = create_app(str(db), None, repo_id="repo_x", repo_root=None, allowed_hosts=_HOSTS)
    with TestClient(app) as client:
        client.get("/api/chain-status")
    assert db.exists()  # RW path created it; the read_only path above never creates/writes


def test_read_only_dashboard_api_missing_db_is_controlled_unavailable(tmp_path):
    app = create_app(str(tmp_path / "missing.db"), None, repo_id="repo_x", repo_root=None,
                     read_only=True, allowed_hosts=_HOSTS)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/chain-status")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "assessment store unavailable"
