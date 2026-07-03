"""`pebra` CLI entry point (Architecture §3) — argparse dispatch over the use-case surfaces."""

from __future__ import annotations

import argparse
from typing import Sequence

from pebra.cli import accept_risk as accept_risk_cmd
from pebra.cli import agent_init as agent_init_cmd
from pebra.cli import assess as assess_cmd
from pebra.cli import dashboard as dashboard_cmd
from pebra.cli import gate_check as gate_check_cmd
from pebra.cli import graph_stats as graph_stats_cmd
from pebra.cli import learn as learn_cmd
from pebra.cli import promote as promote_cmd
from pebra.cli import record_outcome as record_outcome_cmd
from pebra.cli import scorecard as scorecard_cmd
from pebra.cli import setup_graph as setup_graph_cmd
from pebra.cli import verify as verify_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pebra", description="PEBRA — pre-edit benefit-risk assessment.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    assess_cmd.register(subparsers)
    accept_risk_cmd.register(subparsers)
    agent_init_cmd.register(subparsers)
    verify_cmd.register(subparsers)
    record_outcome_cmd.register(subparsers)
    learn_cmd.register(subparsers)
    promote_cmd.register(subparsers)
    scorecard_cmd.register(subparsers)
    dashboard_cmd.register(subparsers)
    setup_graph_cmd.register(subparsers)
    graph_stats_cmd.register(subparsers)
    gate_check_cmd.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
