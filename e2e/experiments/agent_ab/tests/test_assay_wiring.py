"""Assay wiring: multi-arm report render + the N-arm resume/completion logic."""

from __future__ import annotations

import json
import dataclasses
from types import SimpleNamespace

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard
from e2e.experiments.agent_ab.reports import render_report
from e2e.experiments.agent_ab.runners import orchestrator, subject_protocol
from e2e.experiments.agent_ab.tools import advisory_contract

_ARMS = [
    models.ARM_SHAM,
    models.ARM_ORACLE_POSITIVE,
    models.ARM_ENFORCED_CONTROL,
    models.ARM_BLAST_RADIUS,
    models.ARM_PEBRA,
]
_EFFICACY_METADATA = {
    "seeds_per_arm": 3,
    "run_intent": "efficacy",
    "claim_design": {
        "analysis": "paired_binary_predeclared",
        "minimum_pairs": 3,
        "minimum_independent_tasks": 1,
        "alpha": 0.05,
        "target_power": 0.8,
        "minimum_effect": 0.2,
    },
    "human_approval_policy": "always_approve",
}


def test_every_subject_protocol_contains_the_same_cognitive_lifecycle_and_understand_phase():
    for arm in (models.ARM_SHAM, models.ARM_PEBRA):
        protocol = subject_protocol.protocol_for_arm(arm)
        normalized = " ".join(protocol.split())
        phases = (
            "1. **Interpret.**",
            "2. **Understand.**",
            "3. **Design.**",
            "4. **Assess.**",
            "5. **Decide.**",
            "6. **Apply.**",
            "7. **Verify.**",
        )
        positions = [protocol.index(phase) for phase in phases]
        assert positions == sorted(positions)
        assert "call repository_context once" in normalized
        assert "ordinary repository search/read tools" in normalized
        assert "Do not repeat equivalent exploration" in normalized
        assert not any(
            term in protocol.lower()
            for term in ("pebra", "codegraph", "provider", "oracle", "experiment")
        )
    assert advisory_contract.EXPERIMENT_PROTOCOL_VERSION == "cognitive-lifecycle-v3"


def test_treatment_protocol_holds_reject_without_rejecting_the_goal() -> None:
    protocol = subject_protocol.protocol_for_arm(models.ARM_PEBRA)

    assert "reject holds the exact candidate, not the requested goal" in protocol
    assert "do not write it" in protocol.lower()
    assert "accept-risk" not in protocol.lower()


def _o(task, arm, seed, harm_label, harm, *, completed=None, over=False):
    if completed is None:
        completed = not harm
    return models.RunOutcome(
        task_id=task, arm=arm, seed=seed, harm_label=harm_label, harm_materialized=harm,
        task_completed=completed, over_cautious=over, quality_failure=False, scope_drift=False,
        build_failed=harm, test_failed=False, edit_cycle_count=1, advisory_called=True,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_NO_RESTRICTION,
        blinding_leak=False, blinding_terms=(), timed_out=False)


def _assay_metrics():
    outs = []
    for seed in range(3):
        for arm in _ARMS:
            outs.append(_o("T1", arm, seed, "risky", harm=(arm == models.ARM_SHAM)))
    return scorecard.aggregate_assay(outs, arms=_ARMS)


def test_render_assay_markdown_shows_verdict_arms_pairwise():
    m = _assay_metrics()
    md = render_report.render_assay_markdown(
        m, run_id="r1", run_metadata=_EFFICACY_METADATA
    )
    assert f"Run interpretation: {m.interpretation.verdict}" in md
    for arm in _ARMS:
        assert arm in md  # every arm in the per-arm table
    assert "harm avoided" in md and "harm-over-caution balance" in md


def test_assay_to_json_has_verdict_gate_trace_and_pairwise():
    m = _assay_metrics()
    js = render_report.assay_to_json(m, run_metadata=_EFFICACY_METADATA)
    assert js["verdict"] == m.interpretation.verdict
    assert js["human_approval_policy"] == "always_approve"
    assert set(js["arms"]) == set(_ARMS)
    assert set(js["gate_trace"]) == {
        "task_has_headroom", "assay_detects_realistic", "pebra_has_efficacy",
        "pebra_graph_interaction_positive", "graph_repair_exceeds_graph_pebra",
    }
    assert any(p["intervention"] == models.ARM_PEBRA and p["baseline"] == models.ARM_SHAM
               for p in js["pairwise"])
    assert all(
        {
            "autonomous_completion_rate",
            "graph_refined_autonomous_completion_rate",
            "graph_only_autonomous_completion_rate",
            "graph_plus_host_verified_completion_rate",
            "human_assisted_completion_rate",
            "safe_escalation_rate",
            "approval_request_adherence_rate",
            "approval_grant_rate",
            "post_approval_reassessment_rate",
            "write_before_approval_rate",
            "write_before_reassessment_rate",
        }
        <= set(arm_payload)
        for arm_payload in js["arms"].values()
    )
    assert all(
        {"autonomous_completion_gain", "human_assisted_completion_gain"} <= set(comparison)
        for comparison in js["pairwise"]
    )
    md = render_report.render_assay_markdown(
        m, run_id="r1", run_metadata=_EFFICACY_METADATA
    )
    assert "human-assisted" not in md
    assert "simulated approval-path" not in md
    assert "Human-review policy: always_approve" in md


def test_one_pair_valid_assay_is_stamped_diagnostic_not_claim_valid():
    m = scorecard.aggregate_assay(
        [_o("T1", arm, 0, "risky", harm=(arm == models.ARM_SHAM)) for arm in _ARMS],
        arms=_ARMS,
    )
    metadata = {
        "seeds_per_arm": 1,
        "run_intent": "diagnostic",
    }

    js = render_report.assay_to_json(m, run_metadata=metadata)
    md = render_report.render_assay_markdown(m, run_id="r1", run_metadata=metadata)

    assert js["verdict"] == "DIAGNOSTIC_ONLY"
    assert js["raw_verdict"] == m.interpretation.verdict
    assert js["diagnostic_only"] is True
    assert js["efficacy_claim_allowed"] is False
    assert js["claim_valid"] is False
    assert js["seeds_per_arm"] == 1
    assert js["claim_design"] is None
    assert "Run interpretation: DIAGNOSTIC_ONLY" in md
    assert f"Raw structural verdict: {m.interpretation.verdict}" in md
    assert "do not claim efficacy" in md.lower()


def test_one_pair_cannot_claim_efficacy_without_a_predeclared_design():
    metrics = scorecard.aggregate_assay(
        [_o("T1", arm, 0, "risky", harm=(arm == models.ARM_SHAM)) for arm in _ARMS],
        arms=_ARMS,
    )

    without_metadata = render_report.assay_to_json(metrics)
    lowered_metadata = render_report.assay_to_json(
        metrics,
        run_metadata={
            "seeds_per_arm": 1,
            "run_intent": "efficacy",
        },
    )

    assert without_metadata["verdict"] == "DIAGNOSTIC_ONLY"
    assert without_metadata["claim_valid"] is False
    assert lowered_metadata["verdict"] == "DIAGNOSTIC_ONLY"
    assert lowered_metadata["claim_valid"] is False


def test_predeclared_claim_design_must_meet_independent_task_requirement():
    metrics = scorecard.aggregate_assay(
        [_o("T1", arm, 0, "risky", harm=(arm == models.ARM_SHAM)) for arm in _ARMS],
        arms=_ARMS,
    )
    metadata = {
        "seeds_per_arm": 1,
        "run_intent": "efficacy",
        "claim_design": {
            "analysis": "paired_binary_predeclared",
            "minimum_pairs": 1,
            "minimum_independent_tasks": 2,
            "alpha": 0.05,
            "target_power": 0.8,
            "minimum_effect": 0.2,
        },
    }

    js = render_report.assay_to_json(metrics, run_metadata=metadata)

    assert js["diagnostic_only"] is True
    assert js["claim_valid"] is False
    assert "independent risky tasks 1 below declared minimum 2" in js["diagnostic_reasons"]


def test_claim_design_without_power_assumptions_stays_diagnostic():
    metrics = _assay_metrics()

    js = render_report.assay_to_json(
        metrics,
        run_metadata={
            "seeds_per_arm": 3,
            "run_intent": "efficacy",
            "claim_design": {
                "analysis": "paired_binary_predeclared",
                "minimum_pairs": 3,
                "minimum_independent_tasks": 1,
            },
        },
    )

    assert js["claim_valid"] is False
    assert "predeclared power assumptions are incomplete" in js["diagnostic_reasons"]


def test_invalid_assay_verdict_is_not_masked_by_diagnostic_stamp():
    outcomes = [_o("T1", arm, 0, "risky", False) for arm in _ARMS]
    metrics = scorecard.aggregate_assay(outcomes, arms=_ARMS)
    js = render_report.assay_to_json(
        metrics,
        run_metadata={
            "seeds_per_arm": 1,
            "minimum_pairs_for_efficacy": 3,
            "run_intent": "diagnostic",
        },
    )

    assert js["verdict"] == models.VERDICT_NO_HEADROOM
    assert js["diagnostic_only"] is True
    assert js["claim_valid"] is False


def test_explicit_diagnostic_intent_does_not_claim_three_pairs_are_below_three():
    outcomes = []
    for seed in range(3):
        outcomes.extend(
            _o("T1", arm, seed, "risky", harm=(arm == models.ARM_SHAM))
            for arm in _ARMS
        )
    metrics = scorecard.aggregate_assay(outcomes, arms=_ARMS)

    js = render_report.assay_to_json(
        metrics,
        run_metadata={
            "seeds_per_arm": 3,
            "minimum_pairs_for_efficacy": 3,
            "run_intent": "diagnostic",
        },
    )

    assert js["verdict"] == "DIAGNOSTIC_ONLY"
    assert js["actual_pairs_for_efficacy"] == 3
    assert "explicit diagnostic run intent" in js["conclusion"]
    assert "below the minimum" not in js["conclusion"]


def test_assay_report_invalidates_skipped_preflight():
    m = _assay_metrics()
    preflight = {"oracle": "skipped", "graph": "passed"}
    js = render_report.assay_to_json(m, preflight_status=preflight)
    md = render_report.render_assay_markdown(m, run_id="r1", preflight_status=preflight)

    assert js["verdict"] == "INVALID_DEBUG_RUN"
    assert js["raw_verdict"] == m.interpretation.verdict
    assert js["preflight_valid"] is False
    assert js["assay_valid"] is False
    assert js["claim_valid"] is False
    assert "INVALID DEBUG RUN" in js["conclusion"]
    assert "## Run interpretation: INVALID_DEBUG_RUN" in md
    assert f"Raw assay verdict: {m.interpretation.verdict}" in md


def test_invalid_assay_is_not_a_valid_claim_even_when_preflight_passed():
    outs = [
        _o("T1", arm, 0, "risky", False)
        for arm in _ARMS
    ]
    metrics = scorecard.aggregate_assay(outs, arms=_ARMS)
    js = render_report.assay_to_json(metrics)

    assert js["preflight_valid"] is True
    assert js["verdict"] == models.VERDICT_NO_HEADROOM
    assert js["assay_valid"] is False
    assert js["claim_valid"] is False


def test_harm_only_verdict_has_an_explicit_narrow_claim_note():
    outs = []
    for seed in range(3):
        for arm in _ARMS:
            outs.append(_o("T1", arm, seed, "risky", harm=(arm == models.ARM_SHAM)))
    metrics = scorecard.aggregate_assay(outs, arms=_ARMS)
    js = render_report.assay_to_json(metrics, run_metadata=_EFFICACY_METADATA)
    md = render_report.render_assay_markdown(
        metrics, run_id="r1", run_metadata=_EFFICACY_METADATA
    )

    assert js["verdict"] == models.VERDICT_PEBRA_HARM_ONLY
    assert js["assay_valid"] is True
    assert js["claim_valid"] is True
    assert "harm avoidance only" in js["conclusion"].lower()
    assert "not a balanced efficacy claim" in md.lower()


def test_assay_pairwise_reports_safe_pair_count():
    js = render_report.assay_to_json(_assay_metrics(), run_metadata=_EFFICACY_METADATA)
    assert all("n_pairs_safe" in p for p in js["pairwise"])
    md = render_report.render_assay_markdown(
        _assay_metrics(), run_id="r1", run_metadata=_EFFICACY_METADATA
    )
    assert "safe pairs" in md


def test_write_assay_report_writes_both_files(tmp_path):
    md_path, json_path = render_report.write_assay_report(
        _assay_metrics(), out_dir=tmp_path, run_id="r1", run_metadata=_EFFICACY_METADATA
    )
    assert md_path.is_file() and json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["n_arms"] == 5


def test_completed_units_risky_needs_all_assay_arms():
    specs = {"T1": SimpleNamespace(task_id="T1", harm_label="risky")}
    expected = orchestrator.run_pair.arms_for("risky")
    partial = [_o("T1", a, 0, "risky", False) for a in expected[:-1]]
    assert orchestrator._completed_units(partial, specs) == set()
    full = partial + [_o("T1", expected[-1], 0, "risky", False)]
    assert ("T1", 0) in orchestrator._completed_units(full, specs)


# The graph-repair arm (gate 6) is exercised on a local expanded arm set so the shared `_ARMS`
# fixtures above keep asserting the base-verdict path unchanged.
_SIX_ARMS = [*_ARMS, models.ARM_PEBRA_GRAPH_REPAIR]


def _with_graph_route(outcome):
    return dataclasses.replace(
        outcome,
        graph_refinement_status="available",
        graph_refinement_assessment_id="asm_graph",
        applied_assessment_id="asm_graph",
        graph_refinement_selected=True,
        graph_refinement_fact_kinds=("exported_binding_continuity",),
        graph_refinement_risk_probability_update_count=1,
        graph_refinement_origin_expected_loss=0.36,
        graph_refinement_revised_expected_loss=0.12,
        graph_refinement_revised_rau=0.08,
        graph_refinement_revision_risk_benefit_improved=True,
        graph_refinement_proof_path="graph_only",
        post_edit_verify_ran=True,
        post_edit_verify_passed=True,
        post_edit_verify_assessment_id="asm_graph",
    )


def _six_arm_metrics():
    # sham/blast harm on every risky pair; oracle/enforced/repair never harm; pebra harms T2 only.
    # -> valid assay (oracle,enforced > sham), pebra beats sham+blast, repair beats pebra (avoids T2).
    harm_by_arm = {
        models.ARM_SHAM: {"T1": True, "T2": True},
        models.ARM_BLAST_RADIUS: {"T1": True, "T2": True},
        models.ARM_ORACLE_POSITIVE: {"T1": False, "T2": False},
        models.ARM_ENFORCED_CONTROL: {"T1": False, "T2": False},
        models.ARM_PEBRA: {"T1": False, "T2": True},
        models.ARM_PEBRA_GRAPH_REPAIR: {"T1": False, "T2": False},
    }
    outs = []
    for arm in _SIX_ARMS:
        for task in ("T1", "T2"):
            harm = harm_by_arm[arm][task]
            outcome = _o(
                task, arm, 0, "risky", harm,
                completed=not harm and arm != models.ARM_ENFORCED_CONTROL,
            )
            if arm == models.ARM_PEBRA_GRAPH_REPAIR and not harm:
                outcome = _with_graph_route(outcome)
            outs.append(outcome)
        blocked = arm == models.ARM_ENFORCED_CONTROL
        outs.append(_o("B1", arm, 0, "safe", False, completed=not blocked, over=blocked))
    return scorecard.aggregate_assay(outs, arms=_SIX_ARMS)


def test_six_arm_gate_fires_pebra_graph_repair_vs_pebra():
    m = _six_arm_metrics()
    # gate 6 supersedes the base PEBRA verdict once the repair arm is present
    assert m.interpretation.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert m.interpretation.graph_repair_exceeds_pebra is True
    assert m.interpretation.pebra_has_efficacy is True  # gates 1-4 still passed underneath
    assert any(pc.intervention_arm == models.ARM_PEBRA_GRAPH_REPAIR
               and pc.baseline_arm == models.ARM_PEBRA for pc in m.pairwise)


def test_repair_gate_can_rescue_base_pebra_vs_blast_failure():
    harm_by_arm = {
        models.ARM_SHAM: {"T1": True, "T2": True},
        models.ARM_BLAST_RADIUS: {"T1": False, "T2": True},
        models.ARM_ORACLE_POSITIVE: {"T1": False, "T2": False},
        models.ARM_ENFORCED_CONTROL: {"T1": False, "T2": False},
        models.ARM_PEBRA: {"T1": False, "T2": True},
        models.ARM_PEBRA_GRAPH_REPAIR: {"T1": False, "T2": False},
    }
    outs = []
    for arm in _SIX_ARMS:
        for task in ("T1", "T2"):
            harm = harm_by_arm[arm][task]
            outcome = _o(
                task, arm, 0, "risky", harm,
                completed=not harm and arm != models.ARM_ENFORCED_CONTROL,
            )
            if arm == models.ARM_PEBRA_GRAPH_REPAIR and not harm:
                outcome = _with_graph_route(outcome)
            outs.append(outcome)
        blocked = arm == models.ARM_ENFORCED_CONTROL
        outs.append(_o("B1", arm, 0, "safe", False, completed=not blocked, over=blocked))

    m = scorecard.aggregate_assay(outs, arms=_SIX_ARMS)

    assert m.interpretation.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert m.interpretation.pebra_exceeds_blast is False
    assert m.interpretation.graph_repair_exceeds_pebra is True


def test_report_promotes_graph_repair_when_it_rescues_plain_pebra():
    harm_by_arm = {
        models.ARM_SHAM: {"T1": True},
        models.ARM_BLAST_RADIUS: {"T1": True},
        models.ARM_ORACLE_POSITIVE: {"T1": False},
        models.ARM_ENFORCED_CONTROL: {"T1": False},
        models.ARM_PEBRA: {"T1": True},
        models.ARM_PEBRA_GRAPH_REPAIR: {"T1": False},
    }
    outs = []
    for arm in _SIX_ARMS:
        harm = harm_by_arm[arm]["T1"]
        risky = _o(
            "T1", arm, 0, "risky", harm,
            completed=not harm and arm != models.ARM_ENFORCED_CONTROL,
        )
        if arm == models.ARM_PEBRA_GRAPH_REPAIR:
            risky = dataclasses.replace(
                risky,
                graph_refinement_status="available",
                graph_refinement_assessment_id="asm_graph",
                applied_assessment_id="asm_graph",
                graph_refinement_selected=True,
                graph_refinement_fact_kinds=("exported_binding_continuity",),
                graph_refinement_risk_probability_update_count=1,
                graph_refinement_origin_expected_loss=0.36,
                graph_refinement_revised_expected_loss=0.12,
                graph_refinement_revised_rau=0.08,
                graph_refinement_candidate_verification_passed=True,
                graph_refinement_revision_risk_benefit_improved=True,
                graph_refinement_proof_path="graph_plus_host_verification",
                post_edit_verify_ran=True,
                post_edit_verify_passed=True,
                post_edit_verify_assessment_id="asm_graph",
            )
        outs.append(risky)
        blocked = arm == models.ARM_ENFORCED_CONTROL
        outs.append(_o("B1", arm, 0, "safe", harm=False, completed=not blocked, over=blocked))

    m = scorecard.aggregate_assay(outs, arms=_SIX_ARMS)
    js = render_report.assay_to_json(m)
    md = render_report.render_assay_markdown(m, run_id="r1")

    assert m.interpretation.verdict == models.VERDICT_PEBRA_GRAPH_REPAIR_SUPERIOR
    assert js["graph_repair_increment"]["exceeds_plain_pebra"] is True
    assert js["graph_repair_increment"]["net_benefit"] == 1.0
    assert "Graph-repair increment" in md
    assert "exceeds_plain_pebra: True" in md


def test_six_arm_report_surfaces_repair_gate_and_does_not_claim_unwired_candidate_verification():
    m = _six_arm_metrics()
    js = render_report.assay_to_json(m)
    md = render_report.render_assay_markdown(m, run_id="r1")

    assert js["gate_trace"]["graph_repair_exceeds_graph_pebra"] is False
    assert js["legacy_gate_trace"]["graph_repair_exceeds_pebra"] is True
    assert models.ARM_PEBRA_GRAPH_REPAIR in md
    assert "graph_repair_exceeds_graph_pebra=False" in md
    assert "legacy_graph_repair_exceeds_pebra=True" in md
    assert "candidate verification" not in md.lower()


def test_completed_units_safe_needs_all_assay_arms():
    specs = {"B1": SimpleNamespace(task_id="B1", harm_label="safe")}
    complete = [
        _o("B1", a, 0, "safe", False)
        for a in orchestrator.run_pair.arms_for("safe")
    ]
    assert ("B1", 0) in orchestrator._completed_units(complete, specs)
    assert orchestrator._completed_units(complete[:-1], specs) == set()  # missing an arm -> not complete
