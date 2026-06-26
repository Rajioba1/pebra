"""`pebra learn` (Milestone 4d) — trigger shadow learning measurement for one assessment.

SHADOW MEASUREMENT ONLY. This joins the captured prediction manifest to the recorded outcome labels,
computes calibration errors, and records them. It does NOT change any decision parameters — reapplying
learning to decisions is Milestone 5. Separate trigger from assess (Hard Rule).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pebra import composition
from pebra.app import learning_controller

_SHADOW_NOTE = "shadow measurement only; no decision parameters changed"


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "learn", help="Record shadow learning measurement for an assessment (no decisions change)."
    )
    p.add_argument("--assessment-id", required=True, help="The stored assessment id (e.g. asm_1).")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        outcome = learning_controller.measure_learning(
            args.assessment_id, store=ctx.store, learning_port=composition.build_learning_port(ctx)
        )
    except (KeyError, ValueError) as exc:
        # unknown assessment / no outcome recorded / no manifest -> clean exit, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        ctx.store.close()

    if args.as_json:
        print(json.dumps(
            {
                "assessment_id": outcome.assessment_id,
                "observed": outcome.observed,
                "censored": outcome.censored,
                "snapshot_id": outcome.snapshot_id,
                "prediction_errors": len(outcome.prediction_error_ids),
                "mode": _SHADOW_NOTE,
            },
            indent=2, sort_keys=True,
        ))
    else:
        print(
            f"Shadow measurement for {outcome.assessment_id}: "
            f"{outcome.observed} observed, {outcome.censored} censored; snapshot {outcome.snapshot_id}."
        )
        print(f"({_SHADOW_NOTE})")
    return 0
