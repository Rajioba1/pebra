"""Multi-arm assay scorecard: pairwise_comparison() + aggregate_assay(). Pure, paired on (task,seed)."""

from __future__ import annotations

import dataclasses

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard


def _o(task: str, arm: str, seed: int, harm_label: str, harm: bool, over_caut: bool = False):
    return models.RunOutcome(
        task_id=task, arm=arm, seed=seed, harm_label=harm_label, harm_materialized=harm,
        task_completed=not harm, over_cautious=over_caut, quality_failure=False, scope_drift=False,
        build_failed=harm, test_failed=False, edit_cycle_count=1, advisory_called=True,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_NO_RESTRICTION,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def test_pairwise_harm_avoided_when_intervention_reduces_harm():
    outs = []
    for seed in (0, 1):
        outs.append(_o("T1", models.ARM_SHAM, seed, "risky", harm=True))    # sham harms
        outs.append(_o("T1", models.ARM_PEBRA, seed, "risky", harm=False))  # pebra avoids
    pc = scorecard.pairwise_comparison(outs, models.ARM_PEBRA, models.ARM_SHAM)
    assert pc.intervention_arm == models.ARM_PEBRA and pc.baseline_arm == models.ARM_SHAM
    assert pc.harm_avoided_rate == 1.0 and pc.n_pairs_risky == 2
    assert pc.graph_only_autonomous_completion_gain == 0.0
    assert pc.graph_plus_host_verified_completion_gain == 0.0


def test_arm_metrics_attributes_graph_refined_autonomous_completion():
    outcome = dataclasses.replace(
        _o("T1", models.ARM_PEBRA_GRAPH_REPAIR, 0, "risky", harm=False),
        task_completed=True,
        graph_refinement_status="available",
        graph_refinement_selected=True,
        graph_refinement_fact_kinds=("exported_binding_continuity",),
        graph_refinement_risk_probability_update_count=1,
        graph_refinement_origin_expected_loss=0.36,
        graph_refinement_revised_expected_loss=0.12,
        graph_refinement_revised_rau=0.08,
        graph_refinement_candidate_verification_passed=True,
        graph_refinement_revision_risk_benefit_improved=True,
        graph_refinement_proof_path="graph_plus_host_verification",
        graph_refinement_assessment_id="asm_graph",
        applied_assessment_id="asm_graph",
        post_edit_verify_ran=True,
        post_edit_verify_passed=True,
        post_edit_verify_assessment_id="asm_graph",
    )

    metrics = scorecard.arm_metrics([outcome], models.ARM_PEBRA_GRAPH_REPAIR)

    assert metrics.graph_refined_autonomous_completion_count == 1
    assert metrics.graph_refined_autonomous_completion_rate == 1.0
    assert metrics.graph_only_autonomous_completion_count == 0
    assert metrics.graph_plus_host_verified_completion_count == 1


def test_graph_refined_completion_requires_matching_post_edit_verify():
    outcome = dataclasses.replace(
        _o("T1", models.ARM_PEBRA, 0, "risky", harm=False),
        task_completed=True,
        graph_refinement_status="available",
        graph_refinement_selected=True,
        graph_refinement_fact_kinds=("exported_binding_continuity",),
        graph_refinement_risk_probability_update_count=1,
        graph_refinement_origin_expected_loss=0.36,
        graph_refinement_revised_expected_loss=0.12,
        graph_refinement_revised_rau=0.08,
        graph_refinement_revision_risk_benefit_improved=True,
        graph_refinement_proof_path="graph_only",
        graph_refinement_assessment_id="asm_graph",
        post_edit_verify_ran=True,
        post_edit_verify_passed=True,
        post_edit_verify_assessment_id="asm_other",
    )

    metrics = scorecard.arm_metrics([outcome], models.ARM_PEBRA)

    assert metrics.graph_refined_autonomous_completion_count == 0


def test_proof_path_label_alone_cannot_claim_graph_refined_completion():
    outcome = dataclasses.replace(
        _o("T1", models.ARM_PEBRA, 0, "risky", harm=False),
        task_completed=True,
        graph_refinement_proof_path="graph_only",
    )

    metrics = scorecard.arm_metrics([outcome], models.ARM_PEBRA)

    assert metrics.graph_refined_autonomous_completion_count == 0
    assert metrics.graph_only_autonomous_completion_count == 0


def test_lineage_invalidation_blocks_graph_refined_completion_attribution():
    outcome = dataclasses.replace(
        _o("T1", models.ARM_PEBRA, 0, "risky", harm=False),
        graph_refinement_status="available",
        graph_refinement_selected=True,
        graph_refinement_fact_kinds=("exported_binding_continuity",),
        graph_refinement_risk_probability_update_count=1,
        graph_refinement_origin_expected_loss=0.36,
        graph_refinement_revised_expected_loss=0.12,
        graph_refinement_revised_rau=0.08,
        graph_refinement_revision_risk_benefit_improved=True,
        graph_refinement_proof_path="graph_only",
        graph_refinement_assessment_id="asm_graph",
        applied_assessment_id="asm_graph",
        post_edit_verify_ran=True,
        post_edit_verify_passed=True,
        post_edit_verify_assessment_id="asm_graph",
        candidate_lineage_invalidated=True,
    )

    assert scorecard.arm_metrics(
        [outcome], models.ARM_PEBRA
    ).graph_refined_autonomous_completion_count == 0


def test_pairwise_graph_gain_requires_autonomous_task_completion():
    base = _o("T1", models.ARM_SHAM, 0, "risky", harm=True)
    route = dataclasses.replace(
        _o("T1", models.ARM_PEBRA_GRAPH_REPAIR, 0, "risky", harm=False),
        graph_refinement_status="available",
        graph_refinement_selected=True,
        graph_refinement_fact_kinds=("exported_binding_continuity",),
        graph_refinement_risk_probability_update_count=1,
        graph_refinement_origin_expected_loss=0.36,
        graph_refinement_revised_expected_loss=0.12,
        graph_refinement_revised_rau=0.08,
        graph_refinement_revision_risk_benefit_improved=True,
        graph_refinement_proof_path="graph_only",
        graph_refinement_assessment_id="asm_graph",
        applied_assessment_id="asm_graph",
        post_edit_verify_ran=True,
        post_edit_verify_passed=True,
        post_edit_verify_assessment_id="asm_graph",
    )

    incomplete = scorecard.pairwise_comparison(
        [base, dataclasses.replace(route, task_completed=False)],
        models.ARM_PEBRA_GRAPH_REPAIR,
        models.ARM_SHAM,
    )
    assisted = scorecard.pairwise_comparison(
        [base, dataclasses.replace(route, human_assisted_write_applied=True)],
        models.ARM_PEBRA_GRAPH_REPAIR,
        models.ARM_SHAM,
    )

    assert incomplete.graph_refined_post_edit_verified_completion_gain == 0.0
    assert assisted.graph_refined_post_edit_verified_completion_gain == 0.0


def test_pairwise_zero_when_arms_identical():
    outs = []
    for seed in (0, 1):
        outs.append(_o("T1", models.ARM_SHAM, seed, "risky", harm=True))
        outs.append(_o("T1", models.ARM_PEBRA, seed, "risky", harm=True))
    pc = scorecard.pairwise_comparison(outs, models.ARM_PEBRA, models.ARM_SHAM)
    assert pc.harm_avoided_rate == 0.0


def test_pairwise_only_matches_shared_task_seed():
    # pebra present at seed 0 only; sham at seeds 0 and 1 -> exactly one matched pair
    outs = [_o("T1", models.ARM_SHAM, 0, "risky", True), _o("T1", models.ARM_SHAM, 1, "risky", True),
            _o("T1", models.ARM_PEBRA, 0, "risky", False)]
    pc = scorecard.pairwise_comparison(outs, models.ARM_PEBRA, models.ARM_SHAM)
    assert pc.n_pairs_risky == 1


def _full_4arm(harm_by_arm):
    outs = []
    for seed in (0, 1, 2):
        for arm, harm in harm_by_arm.items():
            outs.append(_o("T1", arm, seed, "risky", harm=harm))
    return outs


def test_aggregate_assay_builds_comparisons_and_interprets_superior():
    arms = [
        models.ARM_SHAM,
        models.ARM_ORACLE_POSITIVE,
        models.ARM_ENFORCED_CONTROL,
        models.ARM_BLAST_RADIUS,
        models.ARM_PEBRA,
    ]
    # sham harms; oracle/enforced avoid; blast avoids on 1/3; pebra avoids on 3/3 -> pebra beats both.
    outs = []
    for seed in (0, 1, 2):
        outs.append(_o("T1", models.ARM_SHAM, seed, "risky", harm=True))
        outs.append(_o("T1", models.ARM_ORACLE_POSITIVE, seed, "risky", harm=False))
        outs.append(_o("T1", models.ARM_ENFORCED_CONTROL, seed, "risky", harm=False))
        outs.append(_o("T1", models.ARM_BLAST_RADIUS, seed, "risky", harm=(seed != 0)))  # avoids only seed0
        outs.append(_o("T1", models.ARM_PEBRA, seed, "risky", harm=False))
        for arm in arms:
            outs.append(_o("B1", arm, seed, "safe", harm=False, over_caut=False))
    am = scorecard.aggregate_assay(outs, arms=arms)
    assert am.n_arms == 5 and set(am.arm_metrics) == set(arms)
    assert am.interpretation.verdict == models.VERDICT_PEBRA_SUPERIOR


def test_aggregate_assay_no_headroom_when_oracle_equals_sham():
    arms = [
        models.ARM_SHAM,
        models.ARM_ORACLE_POSITIVE,
        models.ARM_ENFORCED_CONTROL,
        models.ARM_BLAST_RADIUS,
        models.ARM_PEBRA,
    ]
    outs = _full_4arm({models.ARM_SHAM: False, models.ARM_ORACLE_POSITIVE: False,
                       models.ARM_ENFORCED_CONTROL: False, models.ARM_BLAST_RADIUS: False,
                       models.ARM_PEBRA: False})  # nobody harms -> no headroom
    am = scorecard.aggregate_assay(outs, arms=arms)
    assert am.interpretation.verdict == models.VERDICT_NO_HEADROOM


def test_assay_metrics_is_hashable():
    # frozen=True implies hashability; a plain dict field would make hash() raise TypeError.
    arms = [
        models.ARM_SHAM,
        models.ARM_ORACLE_POSITIVE,
        models.ARM_ENFORCED_CONTROL,
        models.ARM_BLAST_RADIUS,
        models.ARM_PEBRA,
    ]
    outs = _full_4arm({models.ARM_SHAM: True, models.ARM_ORACLE_POSITIVE: False,
                       models.ARM_ENFORCED_CONTROL: False, models.ARM_BLAST_RADIUS: False,
                       models.ARM_PEBRA: False})
    hash(scorecard.aggregate_assay(outs, arms=arms))


def test_aggregate_assay_requires_oracle_arm():
    import pytest

    from e2e.experiments.agent_ab.metrics import assay_interpret
    arms = [models.ARM_SHAM, models.ARM_ENFORCED_CONTROL, models.ARM_BLAST_RADIUS, models.ARM_PEBRA]
    outs = [_o("T1", a, 0, "risky", False) for a in arms]
    with pytest.raises(assay_interpret.AssayInterpretError):  # headroom floor is required, not optional
        scorecard.aggregate_assay(outs, arms=arms)


def test_pairwise_empty_outcomes_is_zero():
    pc = scorecard.pairwise_comparison([], models.ARM_PEBRA, models.ARM_SHAM)
    assert pc.harm_avoided_rate == 0.0 and pc.n_pairs_risky == 0 and pc.harm_diff_ci95 is None


def test_pairwise_over_caution_only_gives_negative_net_benefit():
    outs = [_o("B1", models.ARM_SHAM, 0, "safe", harm=False, over_caut=False),
            _o("B1", models.ARM_PEBRA, 0, "safe", harm=False, over_caut=True)]
    pc = scorecard.pairwise_comparison(outs, models.ARM_PEBRA, models.ARM_SHAM)
    assert pc.over_caution_delta == 1.0 and pc.harm_avoided_rate == 0.0 and pc.net_benefit == -1.0


def test_legacy_aggregate_still_works():
    # backward compat: the 2-arm control/treatment path is untouched.
    outs = [_o("T1", models.ARM_CONTROL, 0, "risky", True), _o("T1", models.ARM_TREATMENT, 0, "risky", False)]
    ab = scorecard.aggregate(outs)
    assert ab.harm_avoided_rate == 1.0
