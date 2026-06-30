"""Phase B2 (unit): the pure pieces of the replay — the genesis-vs-learned scoring mechanic and the
corpus partition join. The full real-loop integration is the e2e test (deferred). No DB here."""

from __future__ import annotations

import pytest

from benchmarks.flow import replay
from benchmarks.flow.corpus import export_fixture as corpus_mod
from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot


def _fact(value: float = 0.85) -> SnapshotFact:
    return SnapshotFact(
        fact_id="lrf_1", target_type="risk_binary", target_name="p_success", scope_kind="global",
        scope_value="", specificity_rank=0, value=value, sample_size=120,
        calibration_method="observed_rate_v1", created_at="2026-06-29T00:00:00Z",
        requires_human_ratification=False, scope_json={}, weight=1.0, calibration_quality=1.0,
        scope_change_count=0,
    )


def test_genesis_scoring_leaves_prediction_unchanged():
    inp = replay._minimal_inp(0.70)
    assert apply_snapshot(inp, None).p_success == 0.70  # no snapshot -> baseline prediction


def test_learned_scoring_applies_the_override():
    inp = replay._minimal_inp(0.70)
    out = apply_snapshot(inp, SnapshotBundle("rs_1", (_fact(0.85),)))
    assert out.p_success == pytest.approx(0.85)
    assert out.p_success != 0.70  # apply_snapshot MOVED the prediction (a wiring invariant)


def test_by_partition_splits_and_joins_by_case_id():
    corpus = corpus_mod.build_corpus(n_promote=120, n_score=50)
    promote = replay._by_partition(corpus, "promote")
    score = replay._by_partition(corpus, "score")
    assert len(promote) == 120
    assert len(score) == 50
    case, pred, outcome = promote[0]
    assert case["case_id"] == pred["case_id"] == outcome["case_id"]


def test_outcome_harmful_is_inverse_of_success():
    assert replay._outcome(True).harmful is False
    assert replay._outcome(False).harmful is True
    assert replay._outcome(True).proceeded is True
