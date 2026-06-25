"""`pebra` CLI entry point (Architecture §3) — argparse dispatch over the use-case surfaces."""

from __future__ import annotations

import argparse
from typing import Sequence

from pebra.cli import accept_risk as accept_risk_cmd
from pebra.cli import assess as assess_cmd
from pebra.cli import verify as verify_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pebra", description="PEBRA — pre-edit benefit-risk assessment.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    assess_cmd.register(subparsers)
    accept_risk_cmd.register(subparsers)
    verify_cmd.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
