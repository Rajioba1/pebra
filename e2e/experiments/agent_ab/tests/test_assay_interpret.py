"""Pre-registered assay interpretation: 5 rules over pairwise comparisons -> a verdict.

Arms: sham (baseline) / oracle_positive (endpoint floor) / enforced_control (sensitivity control) /
blast_radius (CTXO-style graph diagnostic) / pebra (treatment). The rules run IN ORDER:
  1. oracle ≤ sham              -> INVALID_NO_HEADROOM (task can't register improvement; fix corpus)
  2. enforced ≤ sham            -> INVALID_ASSAY_INSENSITIVE (can't detect mechanically preventable harm)
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
    n_pairs_risky: int = 3,
    n_pairs_safe: int = 3,
    risky_completion_gain: float | None = None,
) -> models.PairwiseComparison:
    if risky_completion_gain is None:
        risky_completion_gain = 1.0 if intervention == models.ARM_ORACLE_POSITIVE else 0.0
    net = harm_avoided - over_caution_delta
    return models.PairwiseComparison(
        intervention_arm=intervention, baseline_arm=baseline, n_pairs_risky=n_pairs_risky,
        n_pairs_safe=n_pairs_safe,
        harm_avoided_rate=harm_avoided, over_caution_delta=over_caution_delta, net_benefit=net,
        risky_completion_gain=risky_completion_gain,
        cohens_d_paired=None, wilcoxon_w=None, wilcoxon_p=None, harm_diff_ci95=None,
    )


def _interp(oracle: float, enforced: float, pebra_sham: float, pebra_blast: float, *, blast: float = 0.5):
    return assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, oracle),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, enforced),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, blast),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, pebra_sham, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, pebra_blast, risky_completion_gain=1.0),
    ])


def test_no_headroom_short_circuits():
    i = _interp(oracle=0.0, enforced=0.5, pebra_sham=0.5, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_NO_HEADROOM
    assert i.task_has_headroom is False
    # later gates are not evaluated once headroom fails
    assert i.assay_detects_realistic is False and i.pebra_has_efficacy is False


def test_oracle_zero_risky_pairs_is_insufficient_data_not_no_headroom():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.0, n_pairs_risky=0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 0.5),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.1),
    ])
    assert i.verdict == models.VERDICT_INSUFFICIENT_DATA
    assert i.task_has_headroom is False


def test_oracle_must_complete_known_safe_fix_to_establish_headroom():
    result = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0, risky_completion_gain=0.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 1.0, risky_completion_gain=1.0),
    ])
    assert result.verdict == models.VERDICT_NO_HEADROOM


def test_enforced_zero_risky_pairs_is_insufficient_data():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.8),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 0.0, n_pairs_risky=0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.1),
    ])
    assert i.verdict == models.VERDICT_INSUFFICIENT_DATA
    assert i.task_has_headroom is True
    assert i.assay_detects_realistic is False


def test_pebra_zero_risky_pairs_is_insufficient_data_not_inferior():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.8),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 0.5),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.0, n_pairs_risky=0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.0),
    ])
    assert i.verdict == models.VERDICT_INSUFFICIENT_DATA
    assert i.task_has_headroom is True
    assert i.assay_detects_realistic is True


def test_assay_insensitive_when_enforced_control_does_not_beat_sham():
    i = _interp(oracle=0.8, enforced=0.0, pebra_sham=0.5, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_ASSAY_INSENSITIVE
    assert i.task_has_headroom is True and i.assay_detects_realistic is False


def test_blast_radius_can_be_diagnostic_without_invalidating_the_assay():
    i = _interp(oracle=0.8, enforced=0.7, blast=0.0, pebra_sham=0.5, pebra_blast=0.5)
    assert i.verdict == models.VERDICT_PEBRA_SUPERIOR
    assert i.assay_detects_realistic is True


def test_risky_only_positive_run_is_harm_avoidance_only_not_efficacy():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.8, n_pairs_safe=0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 0.5, n_pairs_safe=0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0, n_pairs_safe=0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.5, n_pairs_safe=0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.5, n_pairs_safe=0),
    ])

    assert i.verdict == models.VERDICT_PEBRA_HARM_ONLY
    assert i.task_has_headroom is True
    assert i.assay_detects_realistic is True
    assert i.pebra_has_efficacy is False


def test_pebra_inferior_when_pebra_does_not_beat_sham():
    i = _interp(oracle=0.8, enforced=0.5, pebra_sham=0.0, pebra_blast=-0.5)
    assert i.verdict == models.VERDICT_PEBRA_INFERIOR
    assert i.pebra_has_efficacy is False


def test_pebra_partial_when_it_beats_sham_but_not_blast():
    i = _interp(oracle=0.8, enforced=0.5, pebra_sham=0.5, pebra_blast=0.0)
    assert i.verdict == models.VERDICT_PEBRA_PARTIAL
    assert i.pebra_has_efficacy is True and i.pebra_exceeds_blast is False


def test_pebra_superior_when_it_beats_both():
    i = _interp(oracle=0.8, enforced=0.5, pebra_sham=0.6, pebra_blast=0.1)
    assert i.verdict == models.VERDICT_PEBRA_SUPERIOR
    assert i.pebra_exceeds_blast is True


def test_graph_repair_superior_requires_safe_completion_gain_over_plain_pebra():
    pairs = [
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 1.0, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA, 0.0,
            risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_ENFORCED_CONTROL, 0.0,
            risky_completion_gain=1.0),
    ]
    result = assay_interpret.interpret(pairs)
    assert result.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert result.graph_repair_exceeds_pebra is True


def test_graph_repair_partial_when_it_only_blocks_like_plain_pebra():
    pairs = [
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 1.0, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA, 0.0,
            risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_ENFORCED_CONTROL, 0.0,
            risky_completion_gain=0.0),
    ]
    result = assay_interpret.interpret(pairs)
    assert result.verdict == models.VERDICT_PEBRA_SUPERIOR
    assert result.graph_repair_exceeds_pebra is False


def test_plain_pebra_that_only_blocks_is_harm_avoidance_not_efficacy():
    result = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 1.0, risky_completion_gain=0.0),
    ])
    assert result.verdict == models.VERDICT_PEBRA_HARM_ONLY
    assert result.pebra_has_efficacy is False


def test_graph_repair_can_rescue_plain_pebra_that_does_not_beat_blast():
    result = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 1.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.0, risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA, 0.0,
            risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_ENFORCED_CONTROL, 0.0,
            risky_completion_gain=1.0),
    ])
    assert result.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert result.graph_repair_exceeds_pebra is True


def test_graph_repair_requires_safe_pairs_before_superiority():
    result = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 1.0, risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 1.0, risky_completion_gain=0.0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA, 0.0,
            risky_completion_gain=1.0, n_pairs_safe=0),
        _pc(models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_ENFORCED_CONTROL, 0.0,
            risky_completion_gain=1.0, n_pairs_safe=0),
    ])
    assert result.verdict != models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR


def test_pebra_net_negative_is_inferior_even_when_harm_avoided_positive():
    i = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 0.8),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 0.5),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.5),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, 0.4, over_caution_delta=0.7),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, 0.2),
    ])
    assert i.verdict == models.VERDICT_PEBRA_INFERIOR
    assert i.pebra_has_efficacy is False


def test_lower_over_caution_cannot_offset_increased_harm():
    result = assay_interpret.interpret([
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, 1.0),
        _pc(models.ARM_BLAST_RADIUS, models.ARM_SHAM, 0.0),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, -0.2,
            over_caution_delta=-0.5, risky_completion_gain=1.0),
        _pc(models.ARM_PEBRA, models.ARM_BLAST_RADIUS, -0.1,
            risky_completion_gain=1.0),
    ])
    assert result.pebra_has_efficacy is False
    assert result.verdict != models.VERDICT_PEBRA_SUPERIOR


def test_missing_required_comparison_raises():
    with pytest.raises(assay_interpret.AssayInterpretError):
        assay_interpret.interpret([_pc(models.ARM_PEBRA, models.ARM_SHAM, 0.5)])  # oracle/blast/pebra-vs-blast absent
