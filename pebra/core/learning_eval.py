"""learning_eval (§14.4.1) — pure calibration/eval primitives for the M5d promotion gate.

This module ADDS the metrics the promotion gate needs on top of the per-pair scoring already in
``prediction_error`` (Brier / log-loss / MSE / bias). It does NOT re-derive those — it REUSES
``prediction_error.mean_brier`` / ``mean_log_loss``.

Convention (spec §14.4.1): for risk targets ``y = 1`` means HARMFUL (the edit caused a bug), ``y = 0``
means safe. So ``false_proceed_rate`` = of the harmful edits, what fraction did we let PROCEED; and
``false_block_rate_c0_c2`` = of the SAFE low-criticality edits, what fraction did we hold back.

ECE is reported (reliability), but the spec is explicit it is NOT a hard promotion gate on its own —
it is bin-sensitive. The gate uses proper scoring rules (Brier/log-loss) plus the false-proceed veto.

Pure stdlib; deterministic. numpy/sklearn are validated against in tests/oracles/, never imported here.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from pebra.core.prediction_error import mean_brier, mean_log_loss

_LOW_CRITICALITY = frozenset({"C0", "C1", "C2"})


@dataclass(frozen=True)
class DecisionOutcome:
    """One replayed decision paired with the ground-truth outcome, for safety-rate metrics.

    ``proceeded``      — did the decision let the edit through (PROCEED) vs hold it (anything else)?
    ``harmful``        — y=1: the edit caused harm (a bug). y=0 (False): the edit was safe.
    ``criticality_stage`` — "C0".."C4"; used to scope the false-block rate to low criticality.
    """

    proceeded: bool
    harmful: bool
    criticality_stage: str


def ece(pairs: list[tuple[float, int]], n_bins: int = 10) -> float:
    """Expected Calibration Error over equal-width bins on [0, 1].

    ECE = Σ_b (|b| / N) · |acc(b) − conf(b)|, where conf(b) is the mean predicted probability in bin
    b and acc(b) is the observed positive rate. Equal-width binning (not quantile) is the documented
    choice; ``p == 1.0`` lands in the final bin. Empty bins contribute nothing.
    """
    if not pairs:
        raise ValueError("cannot compute ECE over an empty set of (predicted, actual) pairs")
    if n_bins <= 0:
        raise ValueError(f"n_bins must be > 0, got {n_bins}")
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    # Equal-width bin boundaries are i/n_bins for i in 1..n_bins-1, computed as i*step. bisect_right
    # places p in [edge[k-1], edge[k]); p == 1.0 lands in the last bin (idx == n_bins-1, never out of
    # range). This is the canonical partition; numpy.digitize over np.linspace(0,1,n_bins+1) uses the
    # same float arithmetic, so a float-literal input like 0.6 (== 0.5999…) bins identically either way.
    step = 1.0 / n_bins
    edges = [i * step for i in range(1, n_bins)]
    for p, y in pairs:
        idx = bisect.bisect_right(edges, p)
        bins[idx].append((p, y))
    n = len(pairs)
    total = 0.0
    for b in bins:
        if not b:
            continue
        conf = sum(p for p, _ in b) / len(b)
        acc = sum(y for _, y in b) / len(b)
        total += (len(b) / n) * abs(acc - conf)
    return total


def false_proceed_rate(outcomes: list[DecisionOutcome]) -> float | None:
    """Of the harmful edits, the fraction that were allowed to PROCEED.

    Returns ``None`` when there are no harmful edits (the rate is undefined — the promotion gate
    treats an undefined rate as "did not increase").
    """
    harmful = [o for o in outcomes if o.harmful]
    if not harmful:
        return None
    return sum(1 for o in harmful if o.proceeded) / len(harmful)


def false_block_rate_c0_c2(outcomes: list[DecisionOutcome]) -> float | None:
    """Of the SAFE, low-criticality (C0–C2) edits, the fraction that were held back (not proceeded).

    Returns ``None`` when there are no such edits (undefined).
    """
    safe_low = [
        o for o in outcomes if not o.harmful and o.criticality_stage in _LOW_CRITICALITY
    ]
    if not safe_low:
        return None
    return sum(1 for o in safe_low if not o.proceeded) / len(safe_low)


def lift_lower_is_better(baseline: float, learned: float) -> float:
    """Improvement for lower-is-better metrics (Brier, log-loss): positive = learned is better."""
    return baseline - learned


def lift_higher_is_better(baseline: float, learned: float) -> float:
    """Improvement for higher-is-better metrics: positive = learned is better."""
    return learned - baseline


@dataclass(frozen=True)
class PromotionMetrics:
    """One replay's metric bundle — the promotion evaluator computes this with-fact and without-fact
    and diffs the proper scoring rules while vetoing on the safety rates."""

    n: int
    brier: float
    log_loss: float
    ece: float
    false_proceed_rate: float | None
    false_block_rate_c0_c2: float | None


def compute_promotion_metrics(
    pairs: list[tuple[float, int]],
    outcomes: list[DecisionOutcome],
    n_bins: int = 10,
) -> PromotionMetrics:
    """Bundle the calibration metrics (from ``pairs``) and safety rates (from ``outcomes``).

    ``pairs`` are (predicted_probability, actual_binary) for the proper scoring rules + ECE;
    ``outcomes`` are the replayed decisions for the false-proceed / false-block rates.
    """
    return PromotionMetrics(
        n=len(pairs),
        brier=mean_brier(pairs),
        log_loss=mean_log_loss(pairs),
        ece=ece(pairs, n_bins=n_bins),
        false_proceed_rate=false_proceed_rate(outcomes),
        false_block_rate_c0_c2=false_block_rate_c0_c2(outcomes),
    )
