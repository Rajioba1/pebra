"""`pebra candidate-patch` - convert exact structured edits to a canonical patch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pebra.adapters.candidate_edits import build_candidate_patch


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "candidate-patch",
        help="Build a deterministic unified diff from exact structured replacements.",
    )
    parser.add_argument("edits_file", help="JSON file containing an edits array.")
    parser.add_argument("--repo-root", default=".", help="Repository path (defaults to current directory).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    parser.set_defaults(func=run)


def run(args: Any) -> int:
    raw = json.loads(Path(args.edits_file).read_text(encoding="utf-8"))
    edits = raw.get("edits") if isinstance(raw, dict) else raw
    if not isinstance(edits, list):
        raise ValueError("candidate-patch input must contain an edits array")
    result = build_candidate_patch(args.repo_root, edits)
    payload = {
        "expected_files": list(result.expected_files),
        "proposed_patch": result.patch,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(result.patch, end="")
    return 0
