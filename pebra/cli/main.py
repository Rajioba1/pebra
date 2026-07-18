"""`pebra` CLI entry point (Architecture §3) — argparse dispatch over the use-case surfaces."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from pebra.cli import accept_risk as accept_risk_cmd
from pebra.cli import apply_candidate as apply_candidate_cmd
from pebra.cli import agent_init as agent_init_cmd
from pebra.cli import assess as assess_cmd
from pebra.cli import capabilities as capabilities_cmd
from pebra.cli import candidate_patch as candidate_patch_cmd
from pebra.cli import dashboard as dashboard_cmd
from pebra.cli import dependents as dependents_cmd
from pebra.cli import gate_check as gate_check_cmd
from pebra.cli import finalize_outcome as finalize_outcome_cmd
from pebra.cli import gate_hook as gate_hook_cmd
from pebra.cli import graph_stats as graph_stats_cmd
from pebra.cli import learn as learn_cmd
from pebra.cli import promote as promote_cmd
from pebra.cli import record_outcome as record_outcome_cmd
from pebra.cli import scorecard as scorecard_cmd
from pebra.cli import setup_graph as setup_graph_cmd
from pebra.cli import tui as tui_cmd
from pebra.cli import verify as verify_cmd


def _run_help(args: argparse.Namespace) -> int:
    root: argparse.ArgumentParser = args._help_root
    command_parsers: dict[str, argparse.ArgumentParser] = args._help_commands
    summaries: dict[str, str] = args._help_summaries
    if args.show_all and args.topic:
        args._help_parser.error("a command topic cannot be combined with --all")
    if args.topic:
        print(f"{args.topic}: {summaries[args.topic]}\n")
        command_parsers[args.topic].print_help()
        return 0
    if args.show_all:
        root.print_help()
        for name, command_parser in command_parsers.items():
            if name == "help":
                continue
            print(f"\n{name}: {summaries[name]}\n")
            command_parser.print_help()
        return 0
    root.print_help()
    print("\nDetailed syntax: pebra help <command>")
    print("Complete reference: pebra help --all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pebra", description="PEBRA - pre-edit benefit-risk assessment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    assess_cmd.register(subparsers)
    accept_risk_cmd.register(subparsers)
    apply_candidate_cmd.register(subparsers)
    agent_init_cmd.register(subparsers)
    verify_cmd.register(subparsers)
    record_outcome_cmd.register(subparsers)
    finalize_outcome_cmd.register(subparsers)
    learn_cmd.register(subparsers)
    promote_cmd.register(subparsers)
    scorecard_cmd.register(subparsers)
    dashboard_cmd.register(subparsers)
    tui_cmd.register(subparsers)
    setup_graph_cmd.register(subparsers)
    graph_stats_cmd.register(subparsers)
    capabilities_cmd.register(subparsers)
    candidate_patch_cmd.register(subparsers)
    gate_check_cmd.register(subparsers)
    gate_hook_cmd.register(subparsers)
    dependents_cmd.register(subparsers)
    summaries = {
        action.dest: action.help
        for action in subparsers._choices_actions
    }
    help_parser = subparsers.add_parser(
        "help",
        help="Show command meanings and detailed CLI syntax.",
        description="Show command meanings and detailed CLI syntax.",
    )
    summaries["help"] = "Show command meanings and detailed CLI syntax."
    help_parser.add_argument(
        "topic",
        nargs="?",
        choices=tuple(subparsers.choices),
        help="Command whose full syntax should be displayed.",
    )
    help_parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Display full syntax and option meanings for every command.",
    )
    help_parser.set_defaults(
        func=_run_help,
        _help_root=parser,
        _help_parser=help_parser,
        _help_commands=subparsers.choices,
        _help_summaries=summaries,
    )
    return parser


def _configure_output_streams() -> None:
    """Make CLI rendering fail-soft on legacy consoles without changing UTF-8 output elsewhere."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_output_streams()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] in ("--version", "-V"):
        # Handled before build_parser so it works without a subcommand and never runs git for other
        # commands (provenance shells out to git at most once, only here).
        from pebra.provenance import provenance_line

        print(provenance_line())
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
