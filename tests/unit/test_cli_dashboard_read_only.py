"""`pebra dashboard --read-only`: serve the db read-only WITHOUT resolving a repo root, so no .pebra/ is
initialized anywhere. Requires --db and --repo-id (the identity is supplied, never resolved from disk)."""

from __future__ import annotations

from types import SimpleNamespace

import pebra.cli.dashboard as cli_dash


def _args(**over):
    base = dict(read_only=False, db=None, repo_id=None, repo_root=None, host="127.0.0.1",
                port=None, instance=0, token=False, auth="auto", open=False, dev=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_read_only_requires_db_and_repo_id():
    assert cli_dash.run(_args(read_only=True)) == 1
    assert cli_dash.run(_args(read_only=True, db="x.db")) == 1          # missing --repo-id
    assert cli_dash.run(_args(read_only=True, repo_id="repo_x")) == 1   # missing --db


def test_read_only_requires_existing_db(monkeypatch, tmp_path):
    called = False

    def _serve(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("pebra.dashboard.server.serve", _serve)
    monkeypatch.setattr("pebra.dashboard.server.resolve_dashboard_token", lambda host, mode: None)

    rc = cli_dash.run(_args(read_only=True, db=str(tmp_path / "missing.db"), repo_id="repo_abc"))

    assert rc == 1
    assert called is False


def test_read_only_skips_repo_resolution_and_serves_read_only(monkeypatch, tmp_path):
    seen = {}
    db = tmp_path / "pebra.db"
    db.write_bytes(b"placeholder")

    def _serve(db_path, **kwargs):
        seen["db"] = db_path
        seen.update(kwargs)

    monkeypatch.setattr("pebra.dashboard.server.serve", _serve)
    monkeypatch.setattr("pebra.dashboard.server.resolve_dashboard_token", lambda host, mode: None)
    prepared = []
    monkeypatch.setattr(
        cli_dash.composition,
        "prepare_dashboard_graph_reader",
        lambda repo_root, *, read_only: prepared.append((repo_root, read_only)) or object(),
    )

    class _NoResolve:
        def resolve(self, *a, **k):
            raise AssertionError("RepositoryRegistry.resolve() must NOT run for --read-only (inits .pebra)")

    import pebra.observatory_context as octx

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _NoResolve())

    rc = cli_dash.run(_args(read_only=True, db=str(db), repo_id="repo_abc"))
    assert rc == 0
    assert seen["db"] == str(db)
    assert seen["repo_id"] == "repo_abc"
    assert seen["read_only"] is True
    assert prepared == [(None, True)]


def test_normal_dashboard_prepares_graph_once_and_injects_reader(monkeypatch, tmp_path):
    seen = {}
    db = tmp_path / "pebra.db"
    reader = object()

    monkeypatch.setattr(
        cli_dash.composition,
        "prepare_dashboard_graph_reader",
        lambda repo_root, *, read_only: seen.update(
            prepared=(repo_root, read_only)
        ) or reader,
    )
    monkeypatch.setattr(
        "pebra.dashboard.server.serve",
        lambda _db_path, **kwargs: seen.update(kwargs),
    )
    monkeypatch.setattr("pebra.dashboard.server.resolve_dashboard_token", lambda host, mode: None)

    rc = cli_dash.run(_args(repo_root=str(tmp_path), db=str(db)))

    assert rc == 0
    assert seen["prepared"] == (str(tmp_path.resolve()), False)
    assert seen["graph_reader"] is reader


def test_dashboard_dev_flag_is_passed_to_server(monkeypatch, tmp_path):
    seen = {}
    db = tmp_path / "pebra.db"

    monkeypatch.setattr(
        cli_dash.composition,
        "prepare_dashboard_graph_reader",
        lambda repo_root, *, read_only: object(),
    )
    monkeypatch.setattr(
        "pebra.dashboard.server.serve",
        lambda _db_path, **kwargs: seen.update(kwargs),
    )
    monkeypatch.setattr("pebra.dashboard.server.resolve_dashboard_token", lambda host, mode: None)

    rc = cli_dash.run(_args(repo_root=str(tmp_path), db=str(db), dev=True))

    assert rc == 0
    assert seen["dev_mode"] is True
