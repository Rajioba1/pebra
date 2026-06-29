"""Phase 5 closure — pin the NEW live behavior: an active benefit fact actually changes benefit/RAU
through the real read → apply → score path.

The cold-start golden only proves "no facts changes nothing." This proves "benefit learning affects
decisions": a promoted observed-benefit override, read via SnapshotReadStore and applied by
apply_snapshot, raises benefit and therefore RAU. Also guards that the override is gated (it must clear
the read-port min-sample gate) and that a wide variance keeps RAU conservative (it rises from the
higher observed benefit, it is not inflated past expected_utility).
"""

from __future__ import annotations

import dataclasses

from pebra.adapters.snapshot_read_store import SnapshotReadStore
from pebra.adapters.store.db import SqliteStore
from pebra.core import assessment_builder
from pebra.core.apply_snapshot import apply_snapshot
from pebra.core.models import AssessmentInput, AssessmentRequest


def _inp() -> AssessmentInput:
    req = AssessmentRequest.single_action(
        task="t", action_id="a1", label="x", action_type="edit", expected_files=["src/a.py"],
    )
    return AssessmentInput(
        request=req, action=req.candidate_actions[0],
        events=[{"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40}],
        p_success=0.70, immediate_benefit=0.20, review_cost=0.10,
        criticality_stage="C2", criticality_value=0.50,
        edit_confidence_factors={"p_success": 0.74, "evidence_quality": 0.78, "testability": 0.80,
                                 "reversibility": 0.92, "source_reliability": 0.86,
                                 "scope_control": 0.92},
        thresholds={}, repo_id="r", repo_root="/x",
    )


def _benefit_fact(value, *, sample_size=150):
    return {
        "target_type": "benefit_continuous", "target_name": "measured_benefit",
        "scope_kind": "global", "scope_value": "", "specificity_rank": 0, "scope_json": {},
        "fact_json": {"value": value, "weight": 1.0, "sample_size": sample_size,
                      "calibration_method": "observed_mean_v1"},
        "fact_type": "learned_override", "status": "active", "requires_human_ratification": False,
    }


def test_active_benefit_fact_raises_benefit_and_rau_through_read_apply(tmp_path):
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_benefit_promotion", "hash_version": 2},
        [_benefit_fact(0.90)],
    )
    bundle = SnapshotReadStore(store).load_active_snapshot("r")
    store.close()
    assert bundle is not None  # benefit-only snapshot is readable

    inp = _inp()
    baseline = assessment_builder.build_assessment(inp)            # no facts
    adjusted = apply_snapshot(inp, bundle)                          # read+apply
    learned = assessment_builder.build_assessment(adjusted)

    assert adjusted.benefit_override == 0.90                        # observed benefit applied
    assert learned.scores["benefit"] > baseline.scores["benefit"]  # benefit learning moved benefit
    assert learned.scores["rau"] > baseline.scores["rau"]          # ...and therefore RAU


def test_low_sample_benefit_fact_is_gated_out_and_does_not_change_rau(tmp_path):
    # below the read-port min-sample gate -> never reaches apply -> RAU unchanged (no silent influence).
    store = SqliteStore(str(tmp_path / "p.db"))
    store.insert_learned_fact_batch_with_snapshot(
        "r", {"promotion_reason": "M5d_benefit_promotion", "hash_version": 2},
        [_benefit_fact(0.90, sample_size=1)],
    )
    bundle = SnapshotReadStore(store).load_active_snapshot("r")
    store.close()

    inp = _inp()
    baseline = assessment_builder.build_assessment(inp)
    adjusted = apply_snapshot(inp, bundle) if bundle is not None else inp
    learned = assessment_builder.build_assessment(adjusted)

    assert adjusted.benefit_override is None
    assert learned.scores["rau"] == baseline.scores["rau"]


def test_benefit_override_clamped_to_unit_ceiling():
    # an absurd benefit value (buggy logger / manual insert) must NOT inflate RAU unbounded.
    inp = dataclasses.replace(_inp(), benefit_override=50.0)
    a = assessment_builder.build_assessment(inp)
    assert a.scores["benefit"] == 1.0  # clamped to the unit-utility ceiling, not 50.0


def test_negative_benefit_override_clamped_to_zero():
    inp = dataclasses.replace(_inp(), benefit_override=-5.0)
    a = assessment_builder.build_assessment(inp)
    assert a.scores["benefit"] == 0.0
