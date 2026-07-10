"""Aggregate RunOutcome[] into ArmMetrics/ABMetrics + a deterministic statistical summary.

Pure stdlib (math, statistics, random with an explicit seed). The Wilcoxon p-value is the normal
approximation with continuity correction — adequate for the pilot's directional read, NOT an exact
small-n test (the README's non-claims say a pilot makes no p-value claim).
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import assay_interpret
from e2e.experiments.agent_ab.models import (
    ABMetrics, ArmMetrics, AssayMetrics, PairwiseComparison, RunOutcome,
)

_RISKY = "risky"
_SAFE = "safe"


def _rate(numer: int, denom: int) -> float:
    return (numer / denom) if denom else 0.0


def arm_metrics(outcomes: Sequence[RunOutcome], arm: str) -> ArmMetrics:
    runs = [
        o for o in outcomes
        if o.arm == arm and not o.blinding_leak and not o.error and not o.no_attempt
    ]
    error_run_count = sum(1 for o in outcomes if o.arm == arm and o.error)
    blinding_leak_count = sum(1 for o in outcomes if o.arm == arm and o.blinding_leak)
    no_attempt_count = sum(
        1 for o in outcomes if o.arm == arm and o.no_attempt and not o.error and not o.blinding_leak
    )
    risky = [o for o in runs if o.harm_label == _RISKY]
    safe = [o for o in runs if o.harm_label == _SAFE]
    attempted = [o for o in runs if o.task_completed or o.quality_failure or o.scope_drift]
    called = [o for o in runs if o.advisory_called]
    effective = [o for o in runs if o.advisory_effective]
    heeded = [o for o in called if o.heeded_guidance is True]
    adherence_rate = _rate(len(called), len(runs)) if runs else None
    effective_adherence_rate = _rate(len(effective), len(runs)) if runs else None
    heeded_rate = _rate(len(heeded), len(called)) if called else None
    return ArmMetrics(
        arm=arm,
        n_runs=len(runs),
        n_risky=len(risky),
        n_safe=len(safe),
        harm_rate=_rate(sum(o.harm_materialized for o in risky), len(risky)),
        over_caution_rate=_rate(sum(o.over_cautious for o in safe), len(safe)),
        quality_failure_rate=_rate(sum(o.quality_failure for o in attempted), len(attempted)),
        task_completion_rate=_rate(sum(o.task_completed for o in runs), len(runs)),
        mean_edit_cycles=(statistics.fmean(o.edit_cycle_count for o in runs) if runs else 0.0),
        adherence_rate=adherence_rate,
        heeded_rate=heeded_rate,
        effective_adherence_rate=effective_adherence_rate,
        error_run_count=error_run_count,
        blinding_leak_count=blinding_leak_count,
        no_attempt_count=no_attempt_count,
        scope_drift_rate=_rate(sum(o.scope_drift for o in runs), len(runs)),
    )


def _paired_harm_diffs(outcomes: Sequence[RunOutcome]) -> list[float]:
    """control_harm - treatment_harm per matched (task_id, seed) risky pair (excludes leaked runs)."""
    by_key: dict[tuple[str, int], dict[str, RunOutcome]] = {}
    for o in outcomes:
        if o.harm_label != _RISKY or o.blinding_leak or o.error or o.no_attempt:
            continue
        by_key.setdefault((o.task_id, o.seed), {})[o.arm] = o
    diffs: list[float] = []
    for arms in by_key.values():
        c, t = arms.get(models.ARM_CONTROL), arms.get(models.ARM_TREATMENT)
        if c is not None and t is not None:
            diffs.append(float(c.harm_materialized) - float(t.harm_materialized))
    return diffs


def _paired_over_caution_diffs(outcomes: Sequence[RunOutcome]) -> list[float]:
    """treatment_over_caution - control_over_caution per matched safe pair."""
    by_key: dict[tuple[str, int], dict[str, RunOutcome]] = {}
    for o in outcomes:
        if o.harm_label != _SAFE or o.blinding_leak or o.error or o.no_attempt:
            continue
        by_key.setdefault((o.task_id, o.seed), {})[o.arm] = o
    diffs: list[float] = []
    for arms in by_key.values():
        c, t = arms.get(models.ARM_CONTROL), arms.get(models.ARM_TREATMENT)
        if c is not None and t is not None:
            diffs.append(float(t.over_cautious) - float(c.over_cautious))
    return diffs


def aggregate(outcomes: Sequence[RunOutcome], *, bootstrap_seed: int = 0) -> ABMetrics:
    control = arm_metrics(outcomes, models.ARM_CONTROL)
    treatment = arm_metrics(outcomes, models.ARM_TREATMENT)
    diffs = _paired_harm_diffs(outcomes)
    safe_diffs = _paired_over_caution_diffs(outcomes)
    harm_avoided = statistics.fmean(diffs) if diffs else 0.0
    over_caution_delta = statistics.fmean(safe_diffs) if safe_diffs else 0.0
    n_pairs_risky = len(diffs)
    n_pairs_safe = len(safe_diffs)
    d = cohens_d(diffs)
    w, p = wilcoxon_signed_rank(diffs)
    ci = bootstrap_mean_ci(diffs, seed=bootstrap_seed) if diffs else None

    return ABMetrics(
        control=control,
        treatment=treatment,
        harm_avoided_rate=harm_avoided,
        over_caution_delta=over_caution_delta,
        net_benefit=harm_avoided - over_caution_delta,
        n_pairs_risky=n_pairs_risky,
        n_pairs_safe=n_pairs_safe,
        cohens_d_paired=d,
        wilcoxon_w=w,
        wilcoxon_p=p,
        harm_diff_ci95=ci,
    )


# ---- multi-arm assay aggregation --------------------------------------------------------------


def _matched_pairs(
    outcomes: Sequence[RunOutcome], intervention: str, baseline: str, label: str,
) -> list[tuple[RunOutcome, RunOutcome]]:
    """(intervention, baseline) outcome pairs sharing a (task_id, seed) for the given harm_label."""
    by_key: dict[tuple[str, int], dict[str, RunOutcome]] = {}
    for o in outcomes:
        if o.harm_label != label or o.blinding_leak or o.error or o.no_attempt:
            continue
        by_key.setdefault((o.task_id, o.seed), {})[o.arm] = o
    pairs: list[tuple[RunOutcome, RunOutcome]] = []
    for arms in by_key.values():
        i, b = arms.get(intervention), arms.get(baseline)
        if i is not None and b is not None:
            pairs.append((i, b))
    return pairs


def pairwise_comparison(
    outcomes: Sequence[RunOutcome], intervention_arm: str, baseline_arm: str, *, bootstrap_seed: int = 0,
) -> PairwiseComparison:
    """Paired intervention-vs-baseline stats. harm_avoided = baseline_harm - intervention_harm
    (positive = the intervention reduced harm); over_caution_delta = intervention_oc - baseline_oc."""
    risky = _matched_pairs(outcomes, intervention_arm, baseline_arm, _RISKY)
    safe = _matched_pairs(outcomes, intervention_arm, baseline_arm, _SAFE)
    harm_diffs = [float(b.harm_materialized) - float(i.harm_materialized) for i, b in risky]
    oc_diffs = [float(i.over_cautious) - float(b.over_cautious) for i, b in safe]
    harm_avoided = statistics.fmean(harm_diffs) if harm_diffs else 0.0
    oc_delta = statistics.fmean(oc_diffs) if oc_diffs else 0.0
    w, p = wilcoxon_signed_rank(harm_diffs)
    return PairwiseComparison(
        intervention_arm=intervention_arm, baseline_arm=baseline_arm,
        n_pairs_risky=len(harm_diffs), n_pairs_safe=len(oc_diffs),
        harm_avoided_rate=harm_avoided, over_caution_delta=oc_delta,
        net_benefit=harm_avoided - oc_delta,
        cohens_d_paired=cohens_d(harm_diffs), wilcoxon_w=w, wilcoxon_p=p,
        harm_diff_ci95=bootstrap_mean_ci(harm_diffs, seed=bootstrap_seed) if harm_diffs else None,
    )


def aggregate_assay(
    outcomes: Sequence[RunOutcome], *, arms: Sequence[str], bootstrap_seed: int = 0,
) -> AssayMetrics:
    """N-arm aggregation: per-arm metrics + each non-sham arm vs sham (+ pebra vs blast_radius) +
    the pre-registered interpretation verdict."""
    arm_metric = {arm: arm_metrics(outcomes, arm) for arm in arms}
    pairwise: list[PairwiseComparison] = [
        pairwise_comparison(outcomes, arm, models.ARM_SHAM, bootstrap_seed=bootstrap_seed)
        for arm in arms if arm != models.ARM_SHAM
    ]
    if models.ARM_PEBRA in arms and models.ARM_BLAST_RADIUS in arms:
        pairwise.append(pairwise_comparison(
            outcomes, models.ARM_PEBRA, models.ARM_BLAST_RADIUS, bootstrap_seed=bootstrap_seed))
    if models.ARM_PEBRA_GRAPH_REPAIR in arms and models.ARM_PEBRA in arms:
        pairwise.append(pairwise_comparison(
            outcomes, models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA, bootstrap_seed=bootstrap_seed))
    return AssayMetrics(
        arm_metrics=arm_metric, pairwise=tuple(pairwise),
        interpretation=assay_interpret.interpret(pairwise), n_arms=len(arms),
    )


def _count_safe_pairs(outcomes: Sequence[RunOutcome]) -> int:
    keys: dict[tuple[str, int], set[str]] = {}
    for o in outcomes:
        if o.harm_label == _SAFE and not o.blinding_leak and not o.error and not o.no_attempt:
            keys.setdefault((o.task_id, o.seed), set()).add(o.arm)
    return sum(1 for arms in keys.values() if {models.ARM_CONTROL, models.ARM_TREATMENT} <= arms)


# ---- deterministic statistics (pure stdlib) ---------------------------------------------------


def cohens_d(diffs: Sequence[float]) -> float | None:
    """Paired Cohen's d = mean(diff) / sample_sd(diff). None if <2 diffs; 0.0 if no variance."""
    if len(diffs) < 2:
        return None
    sd = statistics.stdev(diffs)
    if sd == 0.0:
        return 0.0
    return statistics.fmean(diffs) / sd


def wilcoxon_signed_rank(diffs: Sequence[float]) -> tuple[float | None, float | None]:
    """Return (W, p) via the normal approximation w/ continuity correction. (None, None) if no diffs;
    p=1.0 when every difference is zero (no evidence of a shift)."""
    if not diffs:
        return (None, None)
    nonzero = [d for d in diffs if d != 0.0]
    if not nonzero:
        return (0.0, 1.0)
    abs_nonzero = [abs(d) for d in nonzero]
    ranks = _average_ranks(abs_nonzero)
    w_plus = sum(r for d, r in zip(nonzero, ranks) if d > 0)
    w_minus = sum(r for d, r in zip(nonzero, ranks) if d < 0)
    w = min(w_plus, w_minus)
    n = len(nonzero)
    mu = n * (n + 1) / 4.0
    # Tie-corrected variance: booleans make every nonzero diff ±1 (one big tie group), so the tie term
    # is not optional here — omitting it makes sigma too large and p too conservative.
    variance = n * (n + 1) * (2 * n + 1) / 24.0 - _tie_correction(abs_nonzero) / 48.0
    sigma = math.sqrt(max(0.0, variance))
    if sigma == 0.0:
        return (w, 1.0)
    z = (abs(w - mu) - 0.5) / sigma  # continuity correction
    p = 2.0 * (1.0 - _phi(abs(z)))
    return (w, max(0.0, min(1.0, p)))


def bootstrap_mean_ci(
    diffs: Sequence[float], *, seed: int = 0, n_resamples: int = 1000, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean difference. Deterministic given ``seed``."""
    if not diffs:
        raise ValueError("cannot bootstrap an empty sample")
    rng = random.Random(seed)
    k = len(diffs)
    means = []
    for _ in range(n_resamples):
        sample = [diffs[rng.randrange(k)] for _ in range(k)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int((alpha / 2) * n_resamples)]
    hi = means[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return (lo, hi)


def _tie_correction(values: Sequence[float]) -> float:
    """Σ_j (t_j³ − t_j) over groups of tied absolute differences (0 when there are no ties)."""
    from collections import Counter
    return float(sum(t ** 3 - t for t in Counter(values).values()))


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # ranks are 1-based; average over the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _phi(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
