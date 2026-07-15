from __future__ import annotations

import argparse

from pebra.cli import main


def _commands() -> tuple[str, ...]:
    parser = main.build_parser()
    action = next(
        value for value in parser._actions
        if isinstance(value, argparse._SubParsersAction)
    )
    return tuple(action.choices)


def test_help_lists_every_live_command_with_discovery_syntax(capsys) -> None:
    assert main.main(["help"]) == 0

    output = capsys.readouterr().out
    for command in _commands():
        assert command in output
    assert "pebra help <command>" in output
    assert "pebra help --all" in output


def test_help_topic_shows_command_meaning_and_exact_syntax(capsys) -> None:
    assert main.main(["help", "apply-candidate"]) == 0

    output = capsys.readouterr().out
    assert "Apply the exact candidate cached for an authorized assessment." in output
    assert "usage: pebra apply-candidate" in output
    assert "--assessment-id" in output


def test_help_all_renders_detailed_syntax_for_every_non_help_command(capsys) -> None:
    assert main.main(["help", "--all"]) == 0

    output = capsys.readouterr().out
    for command in _commands():
        if command != "help":
            assert f"usage: pebra {command}" in output


def test_every_user_facing_argument_has_meaningful_help_text() -> None:
    parser = main.build_parser()
    subcommands = next(
        value for value in parser._actions
        if isinstance(value, argparse._SubParsersAction)
    )

    missing = [
        f"{command}:{action.dest}"
        for command, command_parser in subcommands.choices.items()
        for action in command_parser._actions
        if action.dest != "help" and not action.help
    ]

    assert missing == []
