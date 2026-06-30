"""Phase B1: the fixture corpus is deterministic, partitioned (promote vs score), and rich enough that
promotion will actually fire (>= MIN_CALIBRATION_SAMPLES) and the learned rate clears genesis by a real
margin. Pure authoring — no DB, no pebra loop here."""

from __future__ import annotations

import pytest

from benchmarks.flow.corpus import export_fixture as ef
from pebra.core.constants import MIN_CALIBRATION_SAMPLES


def _partition(corpus, name):
    return [c for c in corpus.cases if c["partition"] == name]


def test_build_corpus_partition_sizes():
    corpus = ef.build_corpus(n_promote=120, n_score=50)
    assert len(_partition(corpus, "promote")) == 120
    assert len(_partition(corpus, "score")) == 50
    assert len(corpus.cases) == len(corpus.predictions) == len(corpus.outcomes) == 170


def test_build_corpus_is_deterministic():
    assert ef.build_corpus() == ef.build_corpus()  # same seed -> identical corpus


def test_predictions_are_all_genesis_p():
    corpus = ef.build_corpus(genesis_p=0.70)
    assert {p["predicted_value"] for p in corpus.predictions} == {0.70}
    assert {p["target_name"] for p in corpus.predictions} == {"p_success"}
    assert {p["target_type"] for p in corpus.predictions} == {"risk_binary"}


def test_all_outcomes_completed():
    corpus = ef.build_corpus()
    assert {o["terminal_status"] for o in corpus.outcomes} == {"completed"}
    assert all(isinstance(o["actual_success"], bool) for o in corpus.outcomes)


def test_promote_empirical_rate_clears_genesis_by_margin():
    # the learned fact = empirical mean of the promote partition; it must beat genesis_p meaningfully,
    # else applying it can't improve the score track.
    corpus = ef.build_corpus(success_rate=0.85, genesis_p=0.70)
    by_id = {o["case_id"]: o["actual_success"] for o in corpus.outcomes}
    promote = _partition(corpus, "promote")
    rate = sum(by_id[c["case_id"]] for c in promote) / len(promote)
    assert rate - corpus.genesis_p >= 0.05


def test_case_ids_align_across_files():
    corpus = ef.build_corpus()
    case_ids = [c["case_id"] for c in corpus.cases]
    assert [p["case_id"] for p in corpus.predictions] == case_ids
    assert [o["case_id"] for o in corpus.outcomes] == case_ids
    assert len(set(case_ids)) == len(case_ids)  # unique


def test_validate_coverage_rejects_too_few_promote():
    thin = ef.build_corpus(n_promote=MIN_CALIBRATION_SAMPLES - 1, n_score=50)
    with pytest.raises(RuntimeError):
        ef.validate_coverage(thin)


def test_validate_coverage_accepts_committed_shape():
    ef.validate_coverage(ef.build_corpus())  # must not raise


def test_write_and_load_roundtrip(tmp_path):
    corpus = ef.build_corpus()
    ef.write_corpus(corpus, tmp_path)
    loaded = ef.load_corpus(tmp_path)
    assert loaded == corpus


def test_committed_fixture_matches_build_corpus():
    # drift guard: the committed JSONL must be byte-identical to build_corpus() output, so the replay
    # never runs on stale/hand-edited fixture data the regen script would not produce.
    assert ef.load_corpus() == ef.build_corpus(), (
        "committed fixture drifted from build_corpus(); "
        "run 'python -m benchmarks.flow.corpus.export_fixture' and commit"
    )
