"""Phase 3b/5c-E — `pebra dashboard` resolves the repo/db and hands off to the server (serve mocked
so the test doesn't block on uvicorn). Needs fastapi to import the server module -> nox only.
"""

from __future__ import annotations

import importlib.util

import pytest

from pebra.cli.main import main

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None, reason="requires fastapi (run via nox)"
)


def test_dashboard_cli_resolves_and_invokes_serve(tmp_path, monkeypatch) -> None:
    from pebra.dashboard import server

    captured: dict = {}
    monkeypatch.setattr(server, "serve", lambda db_path, **kw: captured.update(db=db_path, kw=kw))

    db = str(tmp_path / "x.db")
    rc = main(
        ["dashboard", "--repo-root", str(tmp_path), "--db", db, "--port", "0", "--instance", "2"]
    )
    assert rc == 0
    assert captured["db"] == db
    assert captured["kw"]["requested_port"] == 0
    assert captured["kw"]["instance"] == 2


def _capture_serve(monkeypatch):
    from pebra.dashboard import server

    captured: dict = {}
    monkeypatch.setattr(server, "serve", lambda db_path, **kw: captured.update(called=True, kw=kw))
    return captured


def test_dashboard_cli_loopback_default_is_token_free(tmp_path, monkeypatch) -> None:
    captured = _capture_serve(monkeypatch)
    rc = main(["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"), "--port", "0"])
    assert rc == 0
    assert captured["kw"]["token"] is None  # loopback default => no token


def test_dashboard_cli_auth_token_forces_a_token(tmp_path, monkeypatch) -> None:
    captured = _capture_serve(monkeypatch)
    rc = main(["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"),
               "--port", "0", "--auth", "token"])
    assert rc == 0
    assert isinstance(captured["kw"]["token"], str) and captured["kw"]["token"]


def test_dashboard_cli_token_flag_is_alias_for_auth_token(tmp_path, monkeypatch) -> None:
    captured = _capture_serve(monkeypatch)
    rc = main(["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"),
               "--port", "0", "--token"])
    assert rc == 0
    assert captured["kw"]["token"]


def test_dashboard_cli_auth_none_on_network_bind_fails_loudly(tmp_path, monkeypatch) -> None:
    captured = _capture_serve(monkeypatch)
    rc = main(["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"),
               "--host", "0.0.0.0", "--auth", "none", "--port", "0"])
    assert rc == 1               # fail loudly, non-zero exit
    assert "called" not in captured  # serve never started


def test_dashboard_cli_open_flag_forwards_to_serve(tmp_path, monkeypatch) -> None:
    captured = _capture_serve(monkeypatch)
    rc = main(["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"),
               "--port", "0", "--open"])
    assert rc == 0
    assert captured["kw"]["open_browser"] is True


def test_dashboard_cli_repo_id_override_for_replay(tmp_path, monkeypatch) -> None:
    # A replayed/copied db resolves to a different repo_id (sha1 of the abs path), so the routes would
    # return empty. --repo-id pins the original id explicitly, sidestepping path resolution.
    from pebra.dashboard import server

    captured: dict = {}
    monkeypatch.setattr(server, "serve", lambda db_path, **kw: captured.update(kw=kw))
    rc = main(
        ["dashboard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "x.db"),
         "--repo-id", "deadbeef1234", "--port", "0"]
    )
    assert rc == 0
    assert captured["kw"]["repo_id"] == "deadbeef1234"
