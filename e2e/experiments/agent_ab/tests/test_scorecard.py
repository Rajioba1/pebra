from __future__ import annotations

import pytest

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import scorecard
from e2e.experiments.agent_ab.models import RunOutcome


def _out(task_id, arm, seed, harm_label, *, harm=False, over=False, completed=True,
         quality=False, called=False, heeded=None, cycles=1, leak=False):
    return RunOutcome(
        task_id=task_id, arm=arm, seed=seed, harm_label=harm_label,
        harm_materialized=harm, task_completed=completed, over_cautious=over,
        quality_failure=quality, scope_drift=False, build_failed=harm or quality,
        test_failed=False, edit_cycle_count=cycles,
        advisory_called=called, advisory_decision=None, heeded_guidance=heeded,
        adherence_state=models.ADH_DID_NOT_CALL, blinding_leak=leak, blinding_terms=(),
        timed_out=False,
    )


def test_harm_and_over_caution_rates_use_correct_denominators():
    outs = [
        _out("T1", "control", 0, "risky", harm=True),
        _out("B1", "control", 0, "safe", over=True, completed=False),
    ]
    m = scorecard.arm_metrics(outs, "control")
    assert m.harm_rate == 1.0 and m.over_caution_rate == 1.0
    assert m.n_risky == 1 and m.n_safe == 1


def test_aggregate_pairs_and_net_benefit():
    outs = [
        _out("T1", "control", 0, "risky", harm=True),
        _out("T1", "treatment", 0, "risky", harm=False, called=True, heeded=True),
        _out("B1", "control", 0, "safe", over=False),
        _out("B1", "treatment", 0, "safe", over=False, called=True),
    ]
    m = scorecard.aggregate(outs)
    assert m.harm_avoided_rate == 1.0            # control 1.0 - treatment 0.0
    assert m.over_caution_delta == 0.0
    assert m.net_benefit == 1.0
    assert m.n_pairs_risky == 1 and m.n_pairs_safe == 1
    assert m.treatment.adherence_rate == 1.0


def test_leaked_runs_excluded_from_pairs():
    outs = [
        _out("T1", "control", 0, "risky", harm=True, leak=True),
        _out("T1", "treatment", 0, "risky", harm=False),
    ]
    m = scorecard.aggregate(outs)
    assert m.n_pairs_risky == 0


def test_leaked_runs_excluded_from_headline_rates():
    outs = [
        _out("T1", "control", 0, "risky", harm=True, leak=True),
        _out("T2", "control", 0, "risky", harm=False),
    ]
    m = scorecard.arm_metrics(outs, "control")
    assert m.n_runs == 1
    assert m.harm_rate == 0.0


def test_quality_failure_rate_uses_attempted_runs():
    outs = [
        _out("B1", "treatment", 0, "safe", completed=False, quality=True),
        _out("B2", "treatment", 0, "safe", completed=False, over=True),
    ]
    m = scorecard.arm_metrics(outs, "treatment")
    assert m.quality_failure_rate == 1.0
    assert m.over_caution_rate == 0.5


def test_cohens_d_none_below_two():
    assert scorecard.cohens_d([1.0]) is None
    assert scorecard.cohens_d([1.0, 1.0, 1.0]) == 0.0       # no variance


def test_cohens_d_magnitude():
    # [1,0,1]: mean 2/3, sample sd sqrt(1/3) -> d = 0.6667/0.5774 ≈ 1.1547 (not just "positive")
    assert abs(scorecard.cohens_d([1.0, 0.0, 1.0]) - 1.1547) < 0.001


def test_wilcoxon_edge_cases():
    assert scorecard.wilcoxon_signed_rank([]) == (None, None)
    assert scorecard.wilcoxon_signed_rank([0.0, 0.0]) == (0.0, 1.0)
    w, p = scorecard.wilcoxon_signed_rank([1.0] * 10)       # all positive shift
    assert w == 0.0 and p < 0.05


def test_wilcoxon_tie_corrected_p_n5_all_positive():
    # n=5 all +1 -> one tie group; tie-corrected sigma gives p≈0.037 (uncorrected ≈0.059 would wrongly
    # read as non-significant). This test locks the tie correction in.
    w, p = scorecard.wilcoxon_signed_rank([1.0] * 5)
    assert w == 0.0
    assert abs(p - 0.037) < 0.005


def test_bootstrap_ci_deterministic_and_bounded():
    diffs = [1.0, 0.0, 1.0, 1.0, 0.0]                        # population mean 0.6
    a = scorecard.bootstrap_mean_ci(diffs, seed=7)
    b = scorecard.bootstrap_mean_ci(diffs, seed=7)
    assert a == b                                            # deterministic given the seed
    assert a == (0.2, 1.0)                                   # exact reference (locks the computation)
    assert a[0] > 0.0                                        # rules out a degenerate always-(0.0, 1.0)
    assert a[0] <= 0.6 <= a[1]                               # CI brackets the sample mean (0.6)
    with pytest.raises(ValueError):
        scorecard.bootstrap_mean_ci([])
