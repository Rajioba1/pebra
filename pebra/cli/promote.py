"""`pebra promote` (Phase 5 closure) — run shadow→active learned-fact promotion for a repo.

The write side of the learning loop: reads production calibration rows (live, observed, proceeded),
runs the replay-gated promotion (`promotion_controller.run_promotion`), and writes an active snapshot +
learned facts when the gates pass. Separate trigger from assess (Hard Rule — assess never promotes).
"Nothing to promote" is a normal outcome (exit 0); only real errors exit non-zero.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pebra import composition, learning_composition
from pebra.app import promotion_controller
from pebra.core import promotion_evaluator as pe


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "promote", help="Run shadow-to-active learned-fact promotion for a repo."
    )
    p.add_argument("--repo-root", default=None)
    p.add_argument("--db", default=None)
    p.add_argument(
        "--drift-freeze-threshold",
        type=float,
        default=None,
        help="Pause risk promotion when active-snapshot drift is at or above this threshold.",
    )
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=run)


def _summary(result: Any) -> dict[str, Any]:
    return {
        "repo_id": result.repo_id,
        "promoted": result.promoted,
        "snapshot_id": result.snapshot_id,
        "facts_considered": result.facts_considered,
        "facts_promoted": result.facts_promoted,
        "facts_vetoed": result.facts_vetoed,
        "veto_reasons": result.veto_reasons,
        "fact_ids": result.fact_ids,
        "drift_score": result.drift_score,
        "frozen_due_to_drift": result.frozen_due_to_drift,
    }


def _line(kind: str, result: Any) -> str:
    if result.promoted:
        return (f"{kind}: promoted {result.facts_promoted} fact(s) to {result.snapshot_id} "
                f"({result.facts_considered} considered, {result.facts_vetoed} vetoed).")
    reasons = ", ".join(result.veto_reasons) or "no calibration rows"
    return f"{kind}: nothing promoted ({result.facts_considered} considered; {reasons})."


def run(args: Any) -> int:
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        learning_port = learning_composition.build_learning_port(ctx)
        config = pe.PromotionConfig(
            drift_freeze_threshold=args.drift_freeze_threshold
        )
        # risk and benefit promotion are DECOUPLED (AD-29) — run both; each writes its own snapshot.
        risk = promotion_controller.run_promotion(
            ctx.repo.repo_id, store=ctx.store, learning_port=learning_port, config=config
        )
        benefit = promotion_controller.run_benefit_promotion(
            ctx.repo.repo_id, store=ctx.store, learning_port=learning_port, config=config
        )
        review_cost = promotion_controller.run_review_cost_promotion(
            ctx.repo.repo_id, store=ctx.store, learning_port=learning_port, config=config
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        ctx.store.close()

    if args.as_json:
        print(json.dumps({"risk": _summary(risk), "benefit": _summary(benefit),
                          "review_cost": _summary(review_cost)},
                         indent=2, sort_keys=True))
    else:
        print(_line("risk", risk))
        print(_line("benefit", benefit))
        print(_line("review_cost", review_cost))
    return 0
