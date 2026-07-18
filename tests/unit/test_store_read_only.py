"""SqliteStore(read_only=True) — the dashboard's read-only viewer posture.

Opens the db ``mode=ro``: reads work, the db file is never modified, no schema creation is issued, and
any write fails at the SQLite engine level. WAL-mode SQLite may still create reader sidecars in the db
directory; strict filesystem isolation is provided by the observatory's temp-copy launch path.
"""

from __future__ import annotations

import sqlite3

import pytest

from pebra.adapters.store.db import SqliteStore


def _seed(db) -> None:
    store = SqliteStore(str(db))  # read-write: materializes the schema
    store.close()


def test_read_only_open_does_not_modify_the_db_file(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db)
    before = (db.stat().st_mtime_ns, db.stat().st_size)
    ro = SqliteStore(str(db), read_only=True)
    assert isinstance(ro.chain_status(), dict)  # a read must succeed
    ro.close()
    assert (db.stat().st_mtime_ns, db.stat().st_size) == before  # db file untouched


def test_read_only_never_creates_a_missing_db(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        SqliteStore(str(tmp_path / "nope.db"), read_only=True)  # mode=ro cannot create the file


def test_read_only_rejects_writes(tmp_path):
    db = tmp_path / "pebra.db"
    _seed(db)
    ro = SqliteStore(str(db), read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.insert_risk_snapshot("repo_x", {})  # any write -> "readonly database"
    ro.close()


def test_read_only_constructor_closes_connection_when_pragma_fails(monkeypatch, tmp_path):
    class _FailingConnection:
        closed = False

        def execute(self, _sql):
            raise sqlite3.OperationalError("pragma failed")

        def close(self):
            self.closed = True

    connection = _FailingConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="pragma failed"):
        SqliteStore(str(tmp_path / "pebra.db"), read_only=True)
    assert connection.closed is True
