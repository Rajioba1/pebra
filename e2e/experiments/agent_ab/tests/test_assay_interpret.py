"""Pre-registered 4-arm assay interpretation: 5 rules over pairwise comparisons -> a verdict.

Arms: sham (baseline) / oracle_positive (endpoint floor) / blast_radius (CTXO-style graph positive
control) / pebra (treatment). The rules run IN ORDER — each gate is only meaningful if the prior passed:
  1. oracle ≤ sham              -> INVALID_NO_HEADROOM (task can't register improvement; fix corpus)
  2. blast ≤ sham (headroom ok) -> INVALID_ASSAY_INSENSITIVE (can't detect realistic guidance; PEBRA null uninterpretable)
  3. pebra ≤ sham               -> PEBRA_INFERIOR
  4. pebra ≤ blast              -> PEBRA_EFFICACY_PARTIAL
  5. all pass                   -> PEBRA_SUPERIOR
"""

from __future__ import annotations

import pytest

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import assay_interpret


def _pc(
    intervention: str,
    baseline: str,
    harm_avoided: float,
    *,
    over_caution_delta: float = 0.0,
) -> models.PairwiseComparison:
    net = harm_avoided - over_caution_delta
    return models.PairwiseComparison(
        intervention_arm=intervention, baseline_arm=baseline, n_pairs_risky=3, n_pairs_safe=0,
        harm_avoided_rate=harm_avoided, over_caution_delta=over_caution_delta, net_benefit=net,
        cohens_d_paired=None, wilcoxon_w=None, wilcoxon_p=None, harm_diff_ci95=None,
    )


def _interp(oracle: float, blast: float, pebra_sham: float, pebra_blast: float):
    return assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, oracle),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, blast),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, pebra_sham),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, pebra_blast),
    ])


def test_no_headroom_short_circuits():
    i = _interp(oracle=0.0, blast=0.5, pebra_sham=0.5, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_NO_HEADROOM
    assert i.task_has_headroom is False
    # later gates are not evaluated once headroom fails
    assert i.assay_detects_realistic is False and i.pebra_has_efficacy is False


def test_assay_insensitive_when_blast_does_not_beat_sham():
    i = _interp(oracle=0.8, blast=0.0, pebra_sham=0.5, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_ASSAY_INSENSITIVE
    assert i.task_has_headroom is True and i.assay_detects_realistic is False


def test_pebra_inferior_when_pebra_does_not_beat_sham():
    i = _interp(oracle=0.8, blast=0.5, pebra_sham=0.0, pebra_blast=-0.5)
    assert i.verdict == models.VERDICT_PEBRA_INFERIOR
    assert i.pebra_has_efficacy is False


def test_pebra_partial_when_it_beats_sham_but_not_blast():
    i = _interp(oracle=0.8, blast=0.5, pebra_sham=0.5, pebra_blast=0.0)
    assert i.verdict == models.VERDICT_PEBRA_PARTIAL
    assert i.pebra_has_efficacy is True and i.pebra_exceeds_blast is False


def test_pebra_superior_when_it_beats_both():
    i = _interp(oracle=0.8, blast=0.5, pebra_sham=0.6, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_PEBRA_SUPERIOR
    assert i.pebra_exceeds_blast is True


def test_pebra_net_negative_is_inferior_even_when_harm_avoided_positive():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.8),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.4, over_caution_delta=0.7),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.2),
    ])
    assert i.verdict == models.VERDICT_PEBRA_INFERIOR
    assert i.pebra_has_efficacy is False


def test_missing_required_comparison_raises():
    with pytest.raises(assay_interpret.AssayInterpretError):
        assay_interpret.interpret([_pc(models.ARM_PEBRA, models.ARM_SHAM, 0.5)])  # oracle/blast/pebra-vs-blast absent
