"""Phase 3 (flow tier): the scorecard is a normalized, deterministic JSON summary built ENTIRELY by
delegating to pebra.core. It adds normalization + shaping + the genesis-vs-learned lift; it never
re-derives a metric."""

from __future__ import annotations

import json

import pytest

from benchmarks.flow import scorecard as sc
from pebra.core import learning_eval as le

_PAIRS = [
    (0.90, 1), (0.20, 0), (0.80, 1), (0.10, 0),
    (0.55, 1), (0.45, 0), (0.99, 1), (0.01, 0), (0.70, 0),
]
# harmful edit that proceeded (false proceed) + a safe low-criticality edit that was blocked.
_OUTCOMES = [
    le.DecisionOutcome(proceeded=True, harmful=True, criticality_stage="C3"),
    le.DecisionOutcome(proceeded=False, harmful=False, criticality_stage="C1"),
    le.DecisionOutcome(proceeded=True, harmful=False, criticality_stage="C0"),
]


def test_build_scorecard_delegates_to_core_metrics():
    card = sc.build_scorecard(_PAIRS, _OUTCOMES, label="learned")
    ref = le.compute_promotion_metrics(_PAIRS, _OUTCOMES)
    assert card["schema_version"] == 1
    assert card["label"] == "learned"
    assert card["n"] == len(_PAIRS)
    m = card["metrics"]
    assert m["brier"] == pytest.approx(ref.brier, abs=1e-12)
    assert m["log_loss"] == pytest.approx(ref.log_loss, abs=1e-12)
    assert m["ece"] == pytest.approx(ref.ece, abs=1e-12)
    assert m["false_proceed_rate"] == pytest.approx(ref.false_proceed_rate, abs=1e-12)
    assert m["false_block_rate_c0_c2"] == pytest.approx(ref.false_block_rate_c0_c2, abs=1e-12)


def test_scorecard_floats_normalized_and_json_byte_identical():
    a = sc.to_json(sc.build_scorecard(_PAIRS, _OUTCOMES, label="x"))
    b = sc.to_json(sc.build_scorecard(_PAIRS, _OUTCOMES, label="x"))
    assert a == b  # determinism target
    card = json.loads(a)
    for v in card["metrics"].values():
        if v is not None:
            assert round(v, sc.PRECISION) == v


def test_undefined_safety_rate_serializes_as_null():
    # no harmful edits -> false_proceed_rate is undefined (None) -> JSON null, not a crash.
    outs = [le.DecisionOutcome(proceeded=True, harmful=False, criticality_stage="C0")]
    card = sc.build_scorecard(_PAIRS, outs)
    assert card["metrics"]["false_proceed_rate"] is None
    assert '"false_proceed_rate": null' in sc.to_json(card)


def test_compare_scorecards_reports_lift_genesis_vs_learned():
    # genesis predicts 0.5 for everything (poorly calibrated); learned predicts the truth (well).
    genesis_pairs = [(0.5, y) for _, y in _PAIRS]
    learned_pairs = [(0.99 if y else 0.01, y) for _, y in _PAIRS]
    genesis = sc.build_scorecard(genesis_pairs, _OUTCOMES, label="genesis")
    learned = sc.build_scorecard(learned_pairs, _OUTCOMES, label="learned")
    cmp = sc.compare_scorecards(genesis, learned)
    assert cmp["genesis_label"] == "genesis"
    assert cmp["learned_label"] == "learned"
    # lower-is-better: positive lift means learned improved on genesis.
    assert cmp["lift"]["brier"] > 0
    assert cmp["lift"]["log_loss"] > 0
    assert "false_proceed_rate" in cmp["lift"]
    assert "false_block_rate_c0_c2" in cmp["lift"]
    assert cmp["brier_improved"] is True


def test_compare_scorecards_reports_safety_rate_lift():
    # lower-is-better: learned has fewer false proceeds and fewer low-criticality false blocks.
    genesis = sc.build_scorecard(
        _PAIRS,
        [
            le.DecisionOutcome(proceeded=True, harmful=True, criticality_stage="C3"),
            le.DecisionOutcome(proceeded=True, harmful=True, criticality_stage="C4"),
            le.DecisionOutcome(proceeded=False, harmful=False, criticality_stage="C1"),
            le.DecisionOutcome(proceeded=False, harmful=False, criticality_stage="C2"),
        ],
        label="genesis",
    )
    learned = sc.build_scorecard(
        _PAIRS,
        [
            le.DecisionOutcome(proceeded=False, harmful=True, criticality_stage="C3"),
            le.DecisionOutcome(proceeded=True, harmful=True, criticality_stage="C4"),
            le.DecisionOutcome(proceeded=True, harmful=False, criticality_stage="C1"),
            le.DecisionOutcome(proceeded=False, harmful=False, criticality_stage="C2"),
        ],
        label="learned",
    )
    cmp = sc.compare_scorecards(genesis, learned)
    assert cmp["lift"]["false_proceed_rate"] == pytest.approx(0.5)
    assert cmp["lift"]["false_block_rate_c0_c2"] == pytest.approx(0.5)


def test_compare_scorecards_keeps_undefined_safety_lift_null():
    no_harm = [le.DecisionOutcome(proceeded=True, harmful=False, criticality_stage="C0")]
    no_safe_low = [le.DecisionOutcome(proceeded=True, harmful=True, criticality_stage="C3")]

    cmp_no_harm = sc.compare_scorecards(
        sc.build_scorecard(_PAIRS, no_harm, label="g"),
        sc.build_scorecard(_PAIRS, no_harm, label="l"),
    )
    assert cmp_no_harm["lift"]["false_proceed_rate"] is None

    cmp_no_safe_low = sc.compare_scorecards(
        sc.build_scorecard(_PAIRS, no_safe_low, label="g"),
        sc.build_scorecard(_PAIRS, no_safe_low, label="l"),
    )
    assert cmp_no_safe_low["lift"]["false_block_rate_c0_c2"] is None


def test_brier_improved_never_contradicts_reported_lift():
    # the boolean is derived from the normalized (displayed) lift, so it must agree with its sign — no
    # "lift: 0.0 but improved: true" self-contradiction, and identical scorecards are not "improved".
    genesis = sc.build_scorecard([(0.5, y) for _, y in _PAIRS], _OUTCOMES, label="g")
    learned = sc.build_scorecard([(0.99 if y else 0.01, y) for _, y in _PAIRS], _OUTCOMES, label="l")
    cmp = sc.compare_scorecards(genesis, learned)
    assert cmp["brier_improved"] == (cmp["lift"]["brier"] > 0)

    same = sc.compare_scorecards(genesis, genesis)
    assert same["lift"]["brier"] == 0
    assert same["brier_improved"] is False


def test_compare_is_deterministic_json():
    gen = sc.build_scorecard([(0.5, y) for _, y in _PAIRS], _OUTCOMES, label="g")
    lrn = sc.build_scorecard(_PAIRS, _OUTCOMES, label="l")
    assert sc.to_json(sc.compare_scorecards(gen, lrn)) == sc.to_json(sc.compare_scorecards(gen, lrn))
