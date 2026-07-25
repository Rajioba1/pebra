"""Update-available notification guardrails for CLI surfaces."""

from __future__ import annotations

import argparse

from pebra.cli import main as cli_main


def test_update_notice_allowlist_excludes_machine_protocols_and_json() -> None:
    assert cli_main._update_notice_allowed("help", argparse.Namespace()) is True
    assert cli_main._update_notice_allowed("tui", argparse.Namespace()) is True
    assert cli_main._update_notice_allowed("dashboard", argparse.Namespace()) is True
    assert cli_main._update_notice_allowed("update", argparse.Namespace()) is False
    assert cli_main._update_notice_allowed("update-check", argparse.Namespace()) is False
    assert cli_main._update_notice_allowed("gate-check", argparse.Namespace()) is False
    assert cli_main._update_notice_allowed("gate-hook", argparse.Namespace()) is False
    assert cli_main._update_notice_allowed("assess", argparse.Namespace(as_json=True)) is False
    assert cli_main._update_notice_allowed("verify", argparse.Namespace(as_json=True)) is False


def test_main_prints_update_notice_to_stderr_after_human_command(monkeypatch, capsys) -> None:
    parser = cli_main.build_parser()
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda _argv: argparse.Namespace(
        command="help",
        func=lambda _args: 0,
    ))
    monkeypatch.setattr(cli_main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_main.update_cmd, "refresh_if_allowed", lambda command, args: None)
    monkeypatch.setattr(cli_main.update_cmd, "notice_from_cache", lambda: "PEBRA 0.3.0 is available")

    assert cli_main.main(["help"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PEBRA 0.3.0 is available" in captured.err


def test_main_update_notice_failure_preserves_command_exit(monkeypatch, capsys) -> None:
    parser = cli_main.build_parser()
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda _argv: argparse.Namespace(
        command="help",
        func=lambda _args: 17,
    ))
    monkeypatch.setattr(cli_main.sys.stdout, "isatty", lambda: True)

    def fail_refresh(_command: str, _args: argparse.Namespace) -> None:
        raise OSError("offline")

    monkeypatch.setattr(cli_main.update_cmd, "refresh_if_allowed", fail_refresh)

    assert cli_main.main(["help"]) == 17
    assert capsys.readouterr().err == ""


def test_main_never_prints_update_notice_for_json_command(monkeypatch, capsys) -> None:
    parser = cli_main.build_parser()
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda _argv: argparse.Namespace(
        command="assess",
        as_json=True,
        func=lambda _args: 0,
    ))
    monkeypatch.setattr(cli_main.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_main.update_cmd, "refresh_if_allowed", lambda *_a, **_k: None)
    monkeypatch.setattr(cli_main.update_cmd, "notice_from_cache", lambda: "PEBRA 0.3.0 is available")

    assert cli_main.main(["assess", "--json"]) == 0
    assert capsys.readouterr().err == ""
