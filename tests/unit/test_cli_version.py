from __future__ import annotations

import pytest

from pebra import provenance
from pebra.cli import main


def test_root_help_lists_both_version_flags():
    help_text = main.build_parser().format_help()
    assert "--version" in help_text
    assert "-V" in help_text


@pytest.mark.parametrize("flag", ("--version", "-V"))
def test_version_flag_renders_provenance_lazily(flag, monkeypatch, capsys):
    calls = 0

    def render() -> str:
        nonlocal calls
        calls += 1
        return "PEBRA 0.1.1 (editable abc1234)"

    monkeypatch.setattr(provenance, "provenance_line", render)
    with pytest.raises(SystemExit) as stopped:
        main.main([flag])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == "PEBRA 0.1.1 (editable abc1234)\n"
    assert calls == 1


def test_parser_build_and_help_do_not_compute_provenance(monkeypatch):
    monkeypatch.setattr(
        provenance, "provenance_line", lambda: pytest.fail("provenance must remain lazy"),
    )
    parser = main.build_parser()
    assert "--version" in parser.format_help()
