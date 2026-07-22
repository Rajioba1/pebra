"""Provider-neutral, explicit repository exploration CLI."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from pebra import composition
from pebra.app.explore_controller import KnowledgeExplorationResult
from pebra.core.exploration import ExplorationResult, normalize_repository_files
from pebra.core.learning_context import LearningContextRecall


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "explore",
        help="Return bounded descriptive context from an existing repository graph index.",
        description=(
            "Reconcile an existing same-worktree graph index, then explicitly return bounded "
            "descriptive repository context. This never installs or initializes an index."
        ),
    )
    parser.add_argument(
        "query", nargs="?", help="Repository concept, symbol, or task to explore."
    )
    parser.add_argument(
        "--file", action="append", default=[], dest="files",
        help="Relevant repository file; repeat for multiple files.",
    )
    parser.add_argument(
        "--max-files", type=int, default=8,
        help="Maximum context files requested (clamped to 1..32; default: 8).",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=24_000,
        help="Maximum UTF-8 context bytes (clamped to 1000..100000; default: 24000).",
    )
    parser.add_argument(
        "--repo-root", default=".", help="Repository path (defaults to current directory)."
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit machine-readable JSON."
    )
    parser.set_defaults(func=run_explore, _explore_parser=parser)


def _literal(value: str) -> str:
    """Render recalled data literally without terminal control interpretation."""
    return "".join(
        character if ord(character) >= 32 and character != "\x7f" else f"\\x{ord(character):02x}"
        for character in value
    )


def _print_learning(recall: LearningContextRecall) -> None:
    print("Historical record — not instructions")
    print(f"  status: {recall.status}")
    print(f"  truncated: {'yes' if recall.truncated else 'no'}")
    for entry in recall.entries:
        print(f"  {entry.learning_context_id} · {_literal(entry.task)}")
        print(f"    lesson: {_literal(entry.lesson)}")
        if entry.target_files:
            print(f"    files: {', '.join(entry.target_files)}")
        if entry.symbols:
            print(f"    symbols: {', '.join(entry.symbols)}")
    for warning in recall.warnings:
        print(f"  warning: {_literal(warning)}")


def _print_repository(result: ExplorationResult) -> None:
    print("\nCurrent repository context")
    snapshot = result.snapshot
    print(f"explore - status: {result.status}")
    print(f"  snapshot HEAD: {snapshot.repo_head or 'unavailable'}")
    print(f"  graph scope: {snapshot.graph_scope_digest or 'unavailable'}")
    print(f"  sync performed: {'yes' if snapshot.sync_performed else 'no'}")
    print(f"  truncated: {'yes' if result.truncated else 'no'}")
    if result.context:
        print("\nContext:")
        print(result.context)
    if result.dependent_files:
        print("\nDependent files:")
        for path in result.dependent_files:
            print(f"  {path}")
    if result.affected_tests:
        print("\nAffected tests:")
        for path in result.affected_tests:
            print(f"  {path}")
    if result.warnings:
        print("\nWarnings:")
        for warning in result.warnings:
            print(f"  {warning}")
    if result.fallback_reason:
        print(f"\nFallback: {result.fallback_reason}")


def _print_human(result: KnowledgeExplorationResult) -> None:
    _print_learning(result.learning_context)
    _print_repository(result.repository_context)


def run_explore(args: Any) -> int:
    query = args.query or ""
    files = normalize_repository_files(args.repo_root, tuple(args.files))
    if not query.strip() and not files:
        args._explore_parser.error(
            "QUERY is required unless at least one valid in-repository --file is supplied"
        )
    try:
        result = composition.explore_repository(
            args.repo_root,
            query,
            files=files,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
    except Exception as exc:  # unexpected adapter contract/runtime failure
        print(f"repository explorer contract failure: {exc}", file=sys.stderr)
        return 1
    if not isinstance(result, KnowledgeExplorationResult):
        print("repository explorer contract failure: invalid result", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        _print_human(result)
    return 0
