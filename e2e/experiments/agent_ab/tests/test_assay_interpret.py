"""Pre-registered interpretation of validity, factorial, and mechanism contrasts."""

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
    risky_completion_gain: float = 1.0,
    graph_plus_host_verified_completion_gain: float = 0.0,
) -> models.PairwiseComparison:
    return models.PairwiseComparison(
        intervention_arm=intervention,
        baseline_arm=baseline,
        n_pairs_risky=n_pairs_risky,
        n_pairs_safe=n_pairs_safe,
        harm_avoided_rate=harm_avoided,
        risky_completion_gain=risky_completion_gain,
        over_caution_delta=over_caution_delta,
        net_benefit=harm_avoided - over_caution_delta,
        cohens_d_paired=None,
        wilcoxon_w=None,
        wilcoxon_p=None,
        harm_diff_ci95=None,
        graph_plus_host_verified_completion_gain=graph_plus_host_verified_completion_gain,
    )


def _pairs(
    *,
    oracle: float = 1.0,
    enforced: float = 1.0,
    graph_sham: float = 0.0,
    pebra_sham: float = 0.4,
    graph_pebra_graph: float = 0.5,
    graph_pebra_pebra: float = 0.5,
    product_completion: float = 1.0,
    n_pairs_risky: int = 3,
    n_pairs_safe: int = 3,
) -> list[models.PairwiseComparison]:
    return [
        _pc(models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, oracle,
            n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
        _pc(models.ARM_ENFORCED_CONTROL, models.ARM_SHAM, enforced,
            n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
        _pc(models.ARM_GRAPH_CONTEXT, models.ARM_SHAM, graph_sham,
            n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
        _pc(models.ARM_PEBRA, models.ARM_SHAM, pebra_sham,
            n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
        _pc(models.ARM_PEBRA_GRAPH_CONTEXT, models.ARM_GRAPH_CONTEXT,
            graph_pebra_graph, risky_completion_gain=product_completion,
            n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
        _pc(models.ARM_PEBRA_GRAPH_CONTEXT, models.ARM_PEBRA,
            graph_pebra_pebra, n_pairs_risky=n_pairs_risky, n_pairs_safe=n_pairs_safe),
    ]


def test_no_headroom_short_circuits() -> None:
    result = assay_interpret.interpret(_pairs(oracle=0.0))
    assert result.verdict == models.VERDICT_NO_HEADROOM
    assert result.task_has_headroom is False
    assert result.assay_detects_realistic is False


def test_oracle_zero_pairs_is_insufficient_data() -> None:
    result = assay_interpret.interpret(_pairs(n_pairs_risky=0))
    assert result.verdict == models.VERDICT_INSUFFICIENT_DATA
    assert result.task_has_headroom is False


def test_oracle_must_complete_the_known_safe_fix() -> None:
    pairs = _pairs()
    pairs[0] = _pc(
        models.ARM_ORACLE_POSITIVE, models.ARM_SHAM, 1.0, risky_completion_gain=0.0
    )
    assert assay_interpret.interpret(pairs).verdict == models.VERDICT_NO_HEADROOM


def test_enforced_control_must_detect_preventable_harm() -> None:
    result = assay_interpret.interpret(_pairs(enforced=0.0))
    assert result.verdict == models.VERDICT_ASSAY_INSENSITIVE
    assert result.task_has_headroom is True
    assert result.assay_detects_realistic is False


def test_missing_product_pairs_are_insufficient_data() -> None:
    pairs = _pairs()
    pairs[4] = _pc(
        models.ARM_PEBRA_GRAPH_CONTEXT,
        models.ARM_GRAPH_CONTEXT,
        0.0,
        n_pairs_risky=0,
    )
    assert assay_interpret.interpret(pairs).verdict == models.VERDICT_INSUFFICIENT_DATA


def test_risky_only_result_is_harm_avoidance_not_efficacy() -> None:
    result = assay_interpret.interpret(_pairs(n_pairs_safe=0))
    assert result.verdict == models.VERDICT_PEBRA_HARM_ONLY
    assert result.pebra_has_efficacy is False


def test_graph_pebra_is_inferior_when_it_increases_harm() -> None:
    result = assay_interpret.interpret(_pairs(graph_pebra_graph=-0.1))
    assert result.verdict == models.VERDICT_PEBRA_INFERIOR
    assert result.pebra_has_efficacy is False


def test_graph_pebra_is_inferior_when_over_caution_erases_harm_benefit() -> None:
    pairs = _pairs()
    pairs[4] = _pc(
        models.ARM_PEBRA_GRAPH_CONTEXT,
        models.ARM_GRAPH_CONTEXT,
        0.4,
        over_caution_delta=0.7,
    )
    assert assay_interpret.interpret(pairs).verdict == models.VERDICT_PEBRA_INFERIOR


def test_graph_pebra_that_only_blocks_is_harm_only() -> None:
    result = assay_interpret.interpret(_pairs(product_completion=0.0))
    assert result.verdict == models.VERDICT_PEBRA_HARM_ONLY


def test_positive_product_effect_without_positive_interaction_is_partial() -> None:
    result = assay_interpret.interpret(
        _pairs(graph_sham=0.5, graph_pebra_pebra=0.5)
    )
    assert result.verdict == models.VERDICT_PEBRA_PARTIAL
    assert result.pebra_has_efficacy is True
    assert result.pebra_graph_interaction_positive is False


def test_positive_factorial_interaction_is_superior() -> None:
    result = assay_interpret.interpret(
        _pairs(graph_sham=0.0, graph_pebra_pebra=0.5)
    )
    assert result.verdict == models.VERDICT_PEBRA_SUPERIOR
    assert result.pebra_graph_interaction_positive is True


def test_graph_repair_superiority_uses_the_graph_pebra_rung() -> None:
    pairs = _pairs()
    pairs.append(_pc(
        models.ARM_PEBRA_GRAPH_REPAIR,
        models.ARM_PEBRA_GRAPH_CONTEXT,
        0.0,
        risky_completion_gain=1.0,
        graph_plus_host_verified_completion_gain=1.0,
    ))
    result = assay_interpret.interpret(pairs)
    assert result.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert result.graph_repair_exceeds_graph_pebra is True


def test_generic_completion_cannot_claim_graph_repair_superiority() -> None:
    pairs = _pairs()
    pairs.append(_pc(
        models.ARM_PEBRA_GRAPH_REPAIR,
        models.ARM_PEBRA_GRAPH_CONTEXT,
        0.0,
        risky_completion_gain=1.0,
    ))
    assert assay_interpret.interpret(pairs).verdict != models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR


def test_graph_repair_requires_safe_pairs() -> None:
    pairs = _pairs()
    pairs.append(_pc(
        models.ARM_PEBRA_GRAPH_REPAIR,
        models.ARM_PEBRA_GRAPH_CONTEXT,
        0.0,
        risky_completion_gain=1.0,
        graph_plus_host_verified_completion_gain=1.0,
        n_pairs_safe=0,
    ))
    assert assay_interpret.interpret(pairs).verdict != models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR


def test_missing_required_comparison_raises() -> None:
    with pytest.raises(assay_interpret.AssayInterpretError):
        assay_interpret.interpret(_pairs()[:-1])
