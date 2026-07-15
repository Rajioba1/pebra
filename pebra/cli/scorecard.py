"""`pebra scorecard` — read-only calibration + benefit report.

Reports the per-target-type calibration (risk-binary Brier/log-loss, benefit-binary, benefit-continuous
MSE — kept SEPARATE per AD-29) plus learning-chain counts. Read-only: it never writes and never changes
a decision. With no observed labels yet, each block reports ``pending_min_n``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pebra import composition
from pebra import learning_composition


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "scorecard", help="Report calibration + benefit metrics (read-only)."
    )
    p.add_argument("--repo-root", default=None, help="Repository path (defaults to current directory).")
    p.add_argument("--db", default=None, help="SQLite store path (defaults to <repo>/.pebra/pebra.db).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit machine-readable JSON.")
    p.set_defaults(func=run)


def run(args: Any) -> int:
    ctx = composition.resolve_repo_and_db(args.repo_root or ".", args.db)
    try:
        summary = learning_composition.build_calibration_store(ctx).calibration_data(ctx.repo.repo_id)
        counts = ctx.store.chain_status()["counts"]
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        ctx.store.close()

    payload = {
        "repo_id": ctx.repo.repo_id,
        "calibration": summary,
        "shadow_counts": {
            k: counts[k] for k in ("assessment_predictions", "prediction_errors", "risk_snapshots")
        },
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_scorecard(payload))
    return 0


def _block(title: str, block: dict[str, Any], *, continuous: bool) -> list[str]:
    lines = [f"{title} (N={block.get('n', 0)}):"]
    if block.get("status") != "ok":
        lines.append("  Status:    pending_min_n (no observed labels yet)")
        return lines
    if continuous:
        lines.append(f"  MSE:       {block['mse']:.4f}")
    else:
        lines.append(f"  Brier:     {block['brier']:.4f}")
        lines.append(f"  Log Loss:  {block['log_loss']:.4f}")
    lines.append(f"  Bias:      {block['bias']:+.4f}")
    return lines


def render_scorecard(payload: dict[str, Any]) -> str:
    cal = payload["calibration"]
    counts = payload["shadow_counts"]
    lines = [f"PEBRA Scorecard — repo: {payload['repo_id']}", ""]
    lines += _block("Risk Calibration (binary)", cal["risk_binary"], continuous=False)
    lines.append("")
    lines += _block("Benefit Calibration (binary proxy)", cal["benefit_binary"], continuous=False)
    lines.append("")
    lines += _block("Benefit Calibration (continuous)", cal["benefit_continuous"], continuous=True)
    lines.append("")
    lines += _block("Review-cost Calibration (continuous)", cal["cost_continuous"], continuous=True)
    lines += [
        "",
        f"Labels:  {cal['observed']} observed / {cal['censored']} censored "
        f"of {cal['total']} computed errors",
        "",
        "Learning evidence recorded (excluded from any decision until promoted):",
        f"  Assessment predictions: {counts['assessment_predictions']}",
        f"  Predictions checked:    {counts['prediction_errors']}",
        f"  Learning snapshots:     {counts['risk_snapshots']}",
    ]
    return "\n".join(lines)
