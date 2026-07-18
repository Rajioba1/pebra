"""Unit tests for the `pebra tui` CLI surface (Observatory TUI M2).

The command resolves the same ObservatoryContext the dashboard uses, then hands it to a launch callable.
Textual must never be imported by ordinary CLI parsing — only when the TUI actually launches — so these
tests inject a fake launch callable and never construct a real Textual app.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

import pebra.cli.tui as cli_tui


def _args(**over):
    base = dict(read_only=False, db=None, repo_id=None, repo_root=None)
    base.update(over)
    return SimpleNamespace(**base)


def _noop(_ctx) -> None:
    pass


def test_tui_subcommand_is_registered_and_help_exits_zero() -> None:
    from pebra.cli.main import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["tui", "--help"])
    assert exc.value.code == 0


def test_help_topic_tui_prints_summary(capsys) -> None:
    from pebra.cli.main import main

    assert main(["help", "tui"]) == 0
    assert "tui" in capsys.readouterr().out.lower()


def test_tui_exposes_only_read_flags_no_http() -> None:
    from pebra.cli.main import build_parser

    parser = build_parser()
    # read flags accepted
    parser.parse_args(["tui", "--repo-root", ".", "--db", "x.db", "--repo-id", "r", "--read-only"])
    # HTTP/serving flags rejected (they belong to `pebra dashboard`, never the TUI)
    forbidden = {
        "--host": ["127.0.0.1"],
        "--port": ["9473"],
        "--instance": ["0"],
        "--token": [],
        "--auth": ["none"],
        "--open": [],
    }
    for flag, value in forbidden.items():
        with pytest.raises(SystemExit):
            parser.parse_args(["tui", flag, *value])


def test_read_only_requires_db_and_repo_id() -> None:
    assert cli_tui.run(_args(read_only=True), launch=_noop) == 1
    assert cli_tui.run(_args(read_only=True, db="x.db"), launch=_noop) == 1
    assert cli_tui.run(_args(read_only=True, repo_id="r"), launch=_noop) == 1


def test_read_only_missing_db_fails_before_launch(tmp_path) -> None:
    launched: list = []
    rc = cli_tui.run(
        _args(read_only=True, db=str(tmp_path / "missing.db"), repo_id="r"),
        launch=launched.append,
    )
    assert rc == 1
    assert launched == []


def test_read_only_resolved_context_reaches_launch(tmp_path) -> None:
    db = tmp_path / "pebra.db"
    db.write_bytes(b"placeholder")
    captured: dict = {}
    rc = cli_tui.run(
        _args(read_only=True, db=str(db), repo_id="repo_x", repo_root="/graph"),
        launch=lambda ctx: captured.setdefault("ctx", ctx),
    )
    assert rc == 0
    ctx = captured["ctx"]
    assert ctx.db_path == str(db)
    assert ctx.repo_id == "repo_x"
    assert ctx.repo_root == "/graph"
    assert ctx.read_only is True


def test_normal_mode_resolved_context_reaches_launch(monkeypatch, tmp_path) -> None:
    import pebra.observatory_context as octx

    class _Repo:
        repo_id = "resolved_repo"
        repo_root = str(tmp_path)

    class _Registry:
        def resolve(self, path):
            return _Repo()

    monkeypatch.setattr(octx, "RepositoryRegistry", lambda: _Registry())

    captured: dict = {}
    rc = cli_tui.run(
        _args(read_only=False, repo_root=str(tmp_path)),
        launch=lambda ctx: captured.setdefault("ctx", ctx),
    )
    assert rc == 0
    assert captured["ctx"].repo_id == "resolved_repo"
    assert captured["ctx"].read_only is False


def test_building_parser_does_not_import_textual() -> None:
    # A fresh process: registering every CLI subcommand (incl. tui) must not import textual. The TUI's
    # textual import is deferred to launch, so ordinary parsing/help stays cheap.
    code = (
        "import sys, pebra.cli.main as m; m.build_parser();"
        "assert 'textual' not in sys.modules, "
        "sorted(k for k in sys.modules if k == 'textual' or k.startswith('textual.'))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
