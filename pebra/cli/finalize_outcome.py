"""Host-only outcome finalization from a trusted JSON sidecar."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pebra import composition, learning_composition
from pebra.app import finalize_outcome_controller


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "finalize-outcome",
        help="Record a trusted host outcome, measure it, and run gated promotion.",
    )
    parser.add_argument(
        "--trusted-outcome-file", required=True,
        help="Host-produced JSON outcome evidence to record and evaluate.",
    )
    parser.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    parser.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    parser.set_defaults(func=run)


def _read_sidecar(path: str) -> tuple[str, str, dict[str, Any] | None]:
    payload = json.loads(Path(path).resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trusted outcome sidecar must contain a JSON object")
    assessment_id = payload.get("assessment_id")
    status = payload.get("status")
    detail = payload.get("detail")
    if not isinstance(assessment_id, str) or not assessment_id:
        raise ValueError("trusted outcome sidecar requires a non-empty assessment_id")
    if status not in {"completed", "skipped", "rejected"}:
        raise ValueError("trusted outcome sidecar has an invalid terminal status")
    if detail is not None and not isinstance(detail, dict):
        raise ValueError("trusted outcome sidecar detail must be an object")
    return assessment_id, status, detail


def run(args: Any) -> int:
    try:
        assessment_id, status, detail = _read_sidecar(args.trusted_outcome_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    context = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        outcome = finalize_outcome_controller.finalize_outcome(
            assessment_id,
            status,
            detail=detail,
            store=context.store,
            learning_port=learning_composition.build_learning_port(context),
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        context.store.close()

    payload = {
        "assessment_id": assessment_id,
        "status": status,
        "outcome_recorded": outcome.outcome_recorded,
        "measurement_recorded": outcome.measurement_recorded,
        "observed": outcome.observed,
        "censored": outcome.censored,
        "promotions": {
            key: {"promoted": value.promoted, "snapshot_id": value.snapshot_id}
            for key, value in outcome.promotions.items()
        },
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Finalized {assessment_id}: outcome_recorded={outcome.outcome_recorded}, "
            f"measurement_recorded={outcome.measurement_recorded}."
        )
    return 0
