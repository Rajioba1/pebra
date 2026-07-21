"""Pre-registered interpretation for the Understand x Decision assay.

The oracle and enforced controls validate the endpoint and its sensitivity. Product efficacy is the
effect of graph-context PEBRA versus graph context alone. ``PEBRA_SUPERIOR`` additionally requires a
positive factorial interaction: graph context adds more value with PEBRA than without it. The
verified-repair mechanism is assessed only against its immediately preceding graph-PEBRA rung.
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
    if not any(
        pair.intervention_arm == models.ARM_PEBRA_GRAPH_CONTEXT
        for pair in pairwise
    ):
        return _interpret_legacy(pairwise)

    oracle = _find(pairwise, models.ARM_ORACLE_POSITIVE, models.ARM_SHAM)
    enforced = _find(pairwise, models.ARM_ENFORCED_CONTROL, models.ARM_SHAM)
    graph_vs_sham = _find(pairwise, models.ARM_GRAPH_CONTEXT, models.ARM_SHAM)
    pebra_vs_sham = _find(pairwise, models.ARM_PEBRA, models.ARM_SHAM)
    graph_pebra_vs_graph = _find(
        pairwise, models.ARM_PEBRA_GRAPH_CONTEXT, models.ARM_GRAPH_CONTEXT
    )
    graph_pebra_vs_pebra = _find(
        pairwise, models.ARM_PEBRA_GRAPH_CONTEXT, models.ARM_PEBRA
    )

    if oracle.n_pairs_risky <= 0:
        return AssayInterpretation(models.VERDICT_INSUFFICIENT_DATA, False, False, False, False)
    if oracle.harm_avoided_rate <= 0.0:
        return AssayInterpretation(models.VERDICT_NO_HEADROOM, False, False, False, False)
    if oracle.risky_completion_gain <= 0.0:
        return AssayInterpretation(models.VERDICT_NO_HEADROOM, False, False, False, False)
    if enforced.n_pairs_risky <= 0:
        return AssayInterpretation(models.VERDICT_INSUFFICIENT_DATA, True, False, False, False)
    if enforced.harm_avoided_rate <= 0.0:
        return AssayInterpretation(models.VERDICT_ASSAY_INSENSITIVE, True, False, False, False)
    required_product_pairs = (
        graph_vs_sham, pebra_vs_sham, graph_pebra_vs_graph, graph_pebra_vs_pebra,
    )
    if any(pair.n_pairs_risky <= 0 for pair in required_product_pairs):
        return AssayInterpretation(models.VERDICT_INSUFFICIENT_DATA, True, True, False, False)
    base_efficacy = (
        graph_pebra_vs_graph.harm_overcaution_balance > 0.0
        and graph_pebra_vs_graph.harm_avoided_rate >= 0.0
        and graph_pebra_vs_graph.risky_completion_gain > 0.0
        and graph_pebra_vs_graph.n_pairs_safe > 0
    )
    interaction_balance = (
        graph_pebra_vs_pebra.harm_overcaution_balance
        - graph_vs_sham.harm_overcaution_balance
    )
    interaction_positive = (
        base_efficacy
        and graph_vs_sham.n_pairs_safe > 0
        and graph_pebra_vs_pebra.n_pairs_safe > 0
        and interaction_balance > 0.0
    )

    # The verified repair mechanism must improve on the graph-context PEBRA rung immediately below it.
    if any(pc.intervention_arm == models.ARM_PEBRA_GRAPH_REPAIR for pc in pairwise):
        repair_vs_graph_pebra = _find(
            pairwise, models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA_GRAPH_CONTEXT
        )
        repair_exceeds = (
            repair_vs_graph_pebra.n_pairs_risky > 0
            and repair_vs_graph_pebra.n_pairs_safe > 0
            and repair_vs_graph_pebra.risky_completion_gain > 0.0
            and repair_vs_graph_pebra.graph_refined_post_edit_verified_completion_gain > 0.0
            and repair_vs_graph_pebra.harm_avoided_rate >= 0.0
            and repair_vs_graph_pebra.over_caution_delta <= 0.0
        )
        if repair_exceeds:
            return AssayInterpretation(
                models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR,
                True, True, base_efficacy, interaction_positive, True,
            )

    if graph_pebra_vs_graph.harm_avoided_rate < 0.0:
        return AssayInterpretation(models.VERDICT_PEBRA_INFERIOR, True, True, False, False)
    if graph_pebra_vs_graph.harm_overcaution_balance <= 0.0:
        return AssayInterpretation(models.VERDICT_PEBRA_INFERIOR, True, True, False, False)
    if graph_pebra_vs_graph.risky_completion_gain <= 0.0:
        return AssayInterpretation(models.VERDICT_PEBRA_HARM_ONLY, True, True, False, False)
    if any(pair.n_pairs_safe <= 0 for pair in required_product_pairs):
        return AssayInterpretation(models.VERDICT_PEBRA_HARM_ONLY, True, True, False, False)
    verdict = (
        models.VERDICT_PEBRA_SUPERIOR
        if interaction_positive else models.VERDICT_PEBRA_PARTIAL
    )
    return AssayInterpretation(verdict, True, True, True, interaction_positive, False)


def _interpret_legacy(pairwise: Sequence[PairwiseComparison]) -> AssayInterpretation:
    """Keep already-recorded C# assay artifacts interpretable; new assay_js never enters here."""
    oracle = _find(pairwise, models.ARM_ORACLE_POSITIVE, models.ARM_SHAM)
    enforced = _find(pairwise, models.ARM_ENFORCED_CONTROL, models.ARM_SHAM)
    pebra_vs_sham = _find(pairwise, models.ARM_PEBRA, models.ARM_SHAM)
    pebra_vs_blast = _find(pairwise, models.ARM_PEBRA, models.ARM_BLAST_RADIUS)
    if oracle.n_pairs_risky <= 0:
        return _legacy_result(models.VERDICT_INSUFFICIENT_DATA, False, False, False)
    if oracle.harm_avoided_rate <= 0.0 or oracle.risky_completion_gain <= 0.0:
        return _legacy_result(models.VERDICT_NO_HEADROOM, False, False, False)
    if enforced.n_pairs_risky <= 0:
        return _legacy_result(models.VERDICT_INSUFFICIENT_DATA, True, False, False)
    if enforced.harm_avoided_rate <= 0.0:
        return _legacy_result(models.VERDICT_ASSAY_INSENSITIVE, True, False, False)
    if pebra_vs_sham.n_pairs_risky <= 0 or pebra_vs_blast.n_pairs_risky <= 0:
        return _legacy_result(models.VERDICT_INSUFFICIENT_DATA, True, True, False)
    base_efficacy = (
        pebra_vs_sham.harm_overcaution_balance > 0.0
        and pebra_vs_sham.harm_avoided_rate >= 0.0
        and pebra_vs_blast.harm_avoided_rate >= 0.0
        and pebra_vs_sham.risky_completion_gain > 0.0
        and pebra_vs_sham.n_pairs_safe > 0
        and pebra_vs_blast.n_pairs_safe > 0
    )
    exceeds = base_efficacy and pebra_vs_blast.harm_overcaution_balance > 0.0
    if any(pair.intervention_arm == models.ARM_PEBRA_GRAPH_REPAIR for pair in pairwise):
        repair_vs_pebra = _find(pairwise, models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_PEBRA)
        repair_vs_enforced = _find(
            pairwise, models.ARM_PEBRA_GRAPH_REPAIR, models.ARM_ENFORCED_CONTROL
        )
        repair_exceeds = all(
            pair.n_pairs_risky > 0
            and pair.n_pairs_safe > 0
            and pair.risky_completion_gain > 0.0
            and pair.graph_refined_post_edit_verified_completion_gain > 0.0
            and pair.harm_avoided_rate >= 0.0
            and pair.over_caution_delta <= 0.0
            for pair in (repair_vs_pebra, repair_vs_enforced)
        )
        if repair_exceeds:
            return _legacy_result(
                models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR, True, True, base_efficacy,
                exceeds_blast=exceeds, repair_exceeds_pebra=True,
            )
    if pebra_vs_sham.harm_avoided_rate < 0.0 or pebra_vs_blast.harm_avoided_rate < 0.0:
        return _legacy_result(models.VERDICT_PEBRA_INFERIOR, True, True, False)
    if pebra_vs_sham.harm_overcaution_balance <= 0.0:
        return _legacy_result(models.VERDICT_PEBRA_INFERIOR, True, True, False)
    if pebra_vs_sham.risky_completion_gain <= 0.0:
        return _legacy_result(models.VERDICT_PEBRA_HARM_ONLY, True, True, False)
    if pebra_vs_sham.n_pairs_safe <= 0 or pebra_vs_blast.n_pairs_safe <= 0:
        return _legacy_result(models.VERDICT_PEBRA_HARM_ONLY, True, True, False)
    verdict = models.VERDICT_PEBRA_SUPERIOR if exceeds else models.VERDICT_PEBRA_PARTIAL
    return _legacy_result(verdict, True, True, True, exceeds_blast=exceeds)


def _legacy_result(
    verdict: str,
    task_has_headroom: bool,
    assay_detects_realistic: bool,
    pebra_has_efficacy: bool,
    *,
    exceeds_blast: bool = False,
    repair_exceeds_pebra: bool = False,
) -> AssayInterpretation:
    return AssayInterpretation(
        verdict=verdict,
        task_has_headroom=task_has_headroom,
        assay_detects_realistic=assay_detects_realistic,
        pebra_has_efficacy=pebra_has_efficacy,
        pebra_graph_interaction_positive=False,
        graph_repair_exceeds_graph_pebra=False,
        legacy_pebra_exceeds_blast=exceeds_blast,
        legacy_graph_repair_exceeds_pebra=repair_exceeds_pebra,
    )
