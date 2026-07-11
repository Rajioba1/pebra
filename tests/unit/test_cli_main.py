from __future__ import annotations

import argparse
import io
import os

from pebra.cli import main


class _LegacyStream:
    def __init__(self) -> None:
        self.errors: str | None = None

    def reconfigure(self, *, errors: str) -> None:
        self.errors = errors


def test_cli_configures_legacy_console_output_to_replace_unencodable_text(monkeypatch) -> None:
    stdout = _LegacyStream()
    stderr = _LegacyStream()
    monkeypatch.setattr(main.sys, "stdout", stdout)
    monkeypatch.setattr(main.sys, "stderr", stderr)

    main._configure_output_streams()

    assert stdout.errors == "replace"
    assert stderr.errors == "replace"


def test_cli_output_configuration_is_fail_soft(monkeypatch) -> None:
    class _ClosedStream:
        def reconfigure(self, *, errors: str) -> None:
            raise ValueError("closed")

    monkeypatch.setattr(main.sys, "stdout", _ClosedStream())
    monkeypatch.setattr(main.sys, "stderr", _ClosedStream())

    main._configure_output_streams()


def test_main_reconfigures_cp1252_before_argparse_renders_help_text(monkeypatch) -> None:
    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="cp1252", errors="strict")

    class _Parser:
        def parse_args(self, argv):
            print("risk → benefit")
            return argparse.Namespace(func=lambda args: 0)

    monkeypatch.setattr(main.sys, "stdout", console)
    monkeypatch.setattr(main.sys, "stderr", console)
    monkeypatch.setattr(main, "build_parser", _Parser)

    assert main.main([]) == 0
    console.flush()
    assert output.getvalue().decode("cp1252") == "risk ? benefit" + os.linesep
