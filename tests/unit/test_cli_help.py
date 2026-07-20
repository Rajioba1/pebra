from __future__ import annotations

import argparse
import re
from pathlib import Path

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
    assert "--version" in output
    assert "-V" in output
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


def test_explore_help_documents_all_bounds_and_existing_index_reconciliation(capsys) -> None:
    assert main.main(["help", "explore"]) == 0

    output = capsys.readouterr().out
    assert "usage: pebra explore" in output
    for flag in ("--file", "--max-files", "--max-bytes", "--repo-root", "--json"):
        assert flag in output
    assert "existing same-worktree graph index" in output
    assert "never installs or initializes" in output


def test_command_reference_inventory_matches_live_parser() -> None:
    reference = (
        Path(__file__).resolve().parents[2] / "docs" / "PEBRA_COMMAND_REFERENCE.md"
    ).read_text(encoding="utf-8")
    product = reference.split("## Product CLI", 1)[1].split("## Standard Product Workflows", 1)[0]
    documented = set(re.findall(r"^### `([^`]+)`$", product, flags=re.MULTILINE))

    assert documented == set(_commands())
    assert f"current tree has {len(_commands())} root CLI commands" in reference
    assert "Planned Commands (Not Yet Shipped)" not in reference
