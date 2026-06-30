"""Phase B3 (unit): the no-coercion comparison. Every wiring break must surface as passed=False with a
reason; passing requires ALL invariants. Pure dict logic — no DB."""

from __future__ import annotations

from benchmarks.flow import compare, scorecard as sc
from pebra.core import learning_eval as le


def _card(p: float, label: str) -> dict:
    ys = [1] * 42 + [0] * 8  # 50 score cases, 84% success (matches the fixture)
    pairs = [(p, y) for y in ys]
    outs = [le.DecisionOutcome(proceeded=True, harmful=not bool(y), criticality_stage="C2") for y in ys]
    return sc.build_scorecard(pairs, outs, label=label)


def _genesis(**over) -> dict:
    d = {"scorecard": _card(0.70, "genesis"), "chain_valid": True, "snapshot_loaded": False, "n": 50}
    d.update(over)
    return d


def _learned(**over) -> dict:
    d = {
        "scorecard": _card(0.85, "learned"), "chain_valid": True, "promotion_fired": True,
        "snapshot_loaded": True, "n_promote_rows": 120, "veto_reasons": [],
    }
    d.update(over)
    return d


def test_passes_when_wiring_intact_and_learned_better():
    art = compare.compare_replay(_genesis(), _learned())
    assert art["passed"] is True
    assert art["failure_reasons"] == []
    assert art["corpus_note"]  # honesty label is always present
    assert "assess_controller" in art["cycle_note"]
    assert art["calibration_quality"]["claim_scope"] == "synthetic_fixture_only"
    assert art["calibration_quality"]["verdict"] == "improved_on_fixture"
    assert art["calibration_quality"]["note"].endswith("not a quality claim")
    assert art["calibration_quality"]["lift"]["brier"] == art["lift"]["brier"]
    assert art["calibration_quality"]["learned"]["ece"] < art["calibration_quality"]["genesis"]["ece"]


def test_fails_when_promotion_not_fired():
    art = compare.compare_replay(_genesis(), _learned(promotion_fired=False, veto_reasons=["INSUFFICIENT_N"]))
    assert art["passed"] is False
    assert any("PROMOTION_NOT_FIRED" in r for r in art["failure_reasons"])


def test_fails_when_learned_not_better():
    worse = _learned()
    worse["scorecard"] = _card(0.50, "learned")  # 0.50 is further from the 0.84 rate than genesis 0.70
    art = compare.compare_replay(_genesis(), worse)
    assert art["passed"] is False
    assert any("LEARNED_NOT_BETTER" in r or "BRIER_NOT_IMPROVED" in r for r in art["failure_reasons"])


def test_fails_when_learned_chain_invalid():
    art = compare.compare_replay(_genesis(), _learned(chain_valid=False))
    assert art["passed"] is False
    assert any("CHAIN_INVALID: learned" in r for r in art["failure_reasons"])


def test_fails_when_genesis_chain_invalid():
    art = compare.compare_replay(_genesis(chain_valid=False), _learned())
    assert art["passed"] is False
    assert any("CHAIN_INVALID: genesis" in r for r in art["failure_reasons"])


def test_fails_when_genesis_loaded_a_snapshot():
    art = compare.compare_replay(_genesis(snapshot_loaded=True), _learned())
    assert art["passed"] is False
    assert any("GENESIS_LOADED_SNAPSHOT" in r for r in art["failure_reasons"])


def test_fails_when_snapshot_not_loaded_after_promotion():
    art = compare.compare_replay(_genesis(), _learned(snapshot_loaded=False))
    assert art["passed"] is False
    assert any("SNAPSHOT_NOT_LOADED" in r for r in art["failure_reasons"])


def test_fails_when_nondeterministic():
    art = compare.compare_replay(_genesis(), _learned(), deterministic=False)
    assert art["passed"] is False
    assert any("NONDETERMINISTIC" in r for r in art["failure_reasons"])


def test_verdict_fields_present_even_on_failure():
    art = compare.compare_replay(_genesis(), _learned(promotion_fired=False))
    for key in (
        "passed", "failure_reasons", "corpus_note", "genesis_brier", "learned_brier",
        "calibration_quality", "lift",
    ):
        assert key in art
