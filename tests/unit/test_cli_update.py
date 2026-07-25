"""`pebra update` and PyPI update-check behavior."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

from pebra import update_cache as uc
from pebra.cli import update as update_cmd


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _args(**overrides: object) -> argparse.Namespace:
    base = {
        "check": False,
        "as_json": False,
        "run": False,
        "yes": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_fetch_latest_version_uses_pypi_json_and_fails_silent(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_urlopen(url: str, *, timeout: int) -> _Response:
        calls.append((url, timeout))
        return _Response(b'{"info": {"version": "0.3.0"}}')

    monkeypatch.setattr(update_cmd.urllib.request, "urlopen", fake_urlopen)
    assert update_cmd.fetch_latest_version(timeout=2) == "0.3.0"
    assert calls == [(update_cmd.PYPI_JSON_URL, 2)]

    monkeypatch.setattr(update_cmd.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    assert update_cmd.fetch_latest_version(timeout=2) is None


def test_refresh_writes_cache_even_when_pypi_is_unreachable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PEBRA_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(update_cmd.provenance, "version", lambda: "0.2.0")
    monkeypatch.setattr(update_cmd, "fetch_latest_version", lambda timeout=2: None)

    entry = update_cmd.refresh(timeout=2, now=100.0)

    assert entry == uc.UpdateCache(checked_at=100.0, current_version="0.2.0", latest_version=None)
    assert uc.read_cache() == entry


def test_update_default_prints_command_without_spawning(monkeypatch, capsys) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))
    monkeypatch.setattr(update_cmd.subprocess, "run", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(update_cmd.sys, "executable", "python")

    assert update_cmd.run_update(_args()) == 0

    out = capsys.readouterr().out
    assert "python -m pip install --upgrade pebra" in out
    assert spawned == []


def test_update_editable_refuses_without_network_or_subprocess(monkeypatch, capsys) -> None:
    touched = []
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: True)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: touched.append("refresh"))
    monkeypatch.setattr(update_cmd.subprocess, "run", lambda *a, **k: touched.append("run"))

    assert update_cmd.run_update(_args(run=True)) == 2

    assert "editable checkout" in capsys.readouterr().err
    assert touched == []


def test_update_respects_opt_out_without_network_or_subprocess(monkeypatch, capsys) -> None:
    touched = []
    monkeypatch.setenv("PEBRA_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: touched.append("refresh"))
    monkeypatch.setattr(update_cmd.subprocess, "run", lambda *a, **k: touched.append("run"))

    assert update_cmd.run_update(_args(run=True)) == 2

    assert "update checks disabled" in capsys.readouterr().err
    assert touched == []


def test_update_check_respects_opt_out_without_network(monkeypatch, capsys) -> None:
    touched = []
    monkeypatch.setenv("PEBRA_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: touched.append("refresh"))

    assert update_cmd.run_check(argparse.Namespace(no_cache=True, as_json=True)) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"disabled": True, "error": "update checks disabled"}
    assert touched == []


def test_update_check_editable_refuses_without_network(monkeypatch, capsys) -> None:
    touched = []
    monkeypatch.delenv("PEBRA_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: True)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: touched.append("refresh"))

    assert update_cmd.run_check(argparse.Namespace(no_cache=True, as_json=False)) == 2

    assert "editable checkout" in capsys.readouterr().err
    assert touched == []


def test_update_run_requires_interactive_confirmation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))
    monkeypatch.setattr(update_cmd.sys.stdin, "isatty", lambda: False)

    assert update_cmd.run_update(_args(run=True)) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_update_yes_runs_without_interactive_stdin(monkeypatch) -> None:
    executed: list[list[str]] = []
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))
    monkeypatch.setattr(update_cmd.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        update_cmd.subprocess,
        "run",
        lambda cmd, check=False: (executed.append(cmd), SimpleNamespace(returncode=0))[1],
    )

    assert update_cmd.run_update(_args(yes=True)) == 0
    assert executed == [update_cmd.install_command()]


def test_update_json_rejects_run_flags_without_silent_noop(monkeypatch, capsys) -> None:
    touched = []
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))
    monkeypatch.setattr(update_cmd.subprocess, "run", lambda *a, **k: touched.append("run"))

    assert update_cmd.run_update(_args(as_json=True, yes=True)) == 2

    assert "cannot combine --json with --run/--yes" in capsys.readouterr().err
    assert touched == []


def test_update_json_reports_command_and_versions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd.provenance, "version", lambda: "0.2.0")
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))

    assert update_cmd.run_update(_args(as_json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["current_version"] == "0.2.0"
    assert payload["latest_version"] == "0.3.0"
    assert payload["update_available"] is True
    assert payload["command"][-5:] == ["-m", "pip", "install", "--upgrade", "pebra"]


def test_update_json_treats_non_stable_versions_as_not_comparable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd.provenance, "version", lambda: "0.3.0rc1")
    monkeypatch.setattr(
        update_cmd,
        "refresh",
        lambda **_kwargs: uc.UpdateCache(1.0, "0.3.0rc1", "0.3.0"),
    )

    assert update_cmd.run_update(_args(as_json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["update_available"] is False


def test_run_update_executes_pip_after_confirmation(monkeypatch) -> None:
    executed: list[list[str]] = []
    monkeypatch.setattr(update_cmd.provenance, "is_editable", lambda: False)
    monkeypatch.setattr(update_cmd, "refresh", lambda **_kwargs: uc.UpdateCache(1.0, "0.2.0", "0.3.0"))
    monkeypatch.setattr(update_cmd.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(update_cmd, "input", lambda _prompt: "UPGRADE")
    monkeypatch.setattr(
        update_cmd.subprocess,
        "run",
        lambda cmd, check=False: (executed.append(cmd), SimpleNamespace(returncode=7))[1],
    )

    assert update_cmd.run_update(_args(run=True)) == 7
    assert executed == [update_cmd.install_command()]
