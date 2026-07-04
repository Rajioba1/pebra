"""Pre-registered assay interpretation — 5 ordered rules over pairwise comparisons -> a verdict.

Pure (stdlib + sibling models only). The gates run in sequence; each is only meaningful if the prior
passed, so a failing early gate SHORT-CIRCUITS and the later booleans stay False:

  1. oracle_positive ≤ sham  -> INVALID_NO_HEADROOM       (endpoint can't register improvement; fix corpus)
  2. blast_radius   ≤ sham    -> INVALID_ASSAY_INSENSITIVE (can't detect a realistic graph-guidance
                                                            intervention; a PEBRA null is uninterpretable)
  3. pebra net      ≤ sham    -> PEBRA_INFERIOR            (harm avoided does not offset over-caution)
  4. pebra net      ≤ blast   -> PEBRA_EFFICACY_PARTIAL    (helps, but not beyond generic blast-radius)
  5. all pass                 -> PEBRA_SUPERIOR

"Beats" for oracle/blast assay-validity gates = ``harm_avoided_rate > 0``. PEBRA efficacy gates use
``net_benefit > 0`` so safe-task over-caution cannot be hidden behind risky-task harm avoidance.
Wilcoxon/CI are reported alongside but do not gate the verdict in the assay-validation config.
"""

from __future__ import annotations

from collections.abc import Sequence

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.models import AssayInterpretation, PairwiseComparison


class AssayInterpretError(ValueError):
    """A pairwise comparison required by the interpretation rules is absent from the assay results."""


def _find(pairwise: Sequence[PairwiseComparison], intervention: str, baseline: str) -> PairwiseComparison:
    for pc in pairwise:
        if pc.intervention_arm == intervention and pc.baseline_arm == baseline:
            return pc
    raise AssayInterpretError(f"required comparison missing: {intervention} vs {baseline}")


def interpret(pairwise: Sequence[PairwiseComparison]) -> AssayInterpretation:
    oracle = _find(pairwise, models.ARM_ORACLE_POSITIVE, models.ARM_SHAM)
    blast = _find(pairwise, models.ARM_BLAST_RADIUS, models.ARM_SHAM)
    pebra_vs_sham = _find(pairwise, models.ARM_PEBRA, models.ARM_SHAM)
    pebra_vs_blast = _find(pairwise, models.ARM_PEBRA, models.ARM_BLAST_RADIUS)

    if oracle.harm_avoided_rate <= 0.0:
        return AssayInterpretation(models.VERDICT_NO_HEADROOM, False, False, False, False)
    if blast.harm_avoided_rate <= 0.0:
        return AssayInterpretation(models.VERDICT_ASSAY_INSENSITIVE, True, False, False, False)
    if pebra_vs_sham.net_benefit <= 0.0:
        return AssayInterpretation(models.VERDICT_PEBRA_INFERIOR, True, True, False, False)
    exceeds = pebra_vs_blast.net_benefit > 0.0
    verdict = models.VERDICT_PEBRA_SUPERIOR if exceeds else models.VERDICT_PEBRA_PARTIAL
    return AssayInterpretation(verdict, True, True, True, exceeds)
