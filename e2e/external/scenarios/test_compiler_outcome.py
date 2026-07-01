"""Scenario A — real compiler-outcome learning on a REAL C# repo.

A scoped public-API edit (add a required CancellationToken to IWorkspace.CanCloseAsync) passes the
pre-edit assess + the post-edit verify, but BREAKS the build. PEBRA learns from the *compiler's* verdict
(actual_success = build passed), and that one real cycle is the sample that tips the promotion gate —
so it is load-bearing, not decorative. After promotion, a follow-up public-API edit is assessed more
cautiously. The real-vs-seeded split is honest: 1 compiler-judged cycle + 99 authored seeds.
"""

from __future__ import annotations


def test_real_build_failure_is_the_recorded_outcome(compiler_outcome_state):
    s = compiler_outcome_state
    assert s.dotnet_available is True
    assert s.baseline_build_passed is True
    assert s.build_ran is True
    assert s.build_passed is False  # compiler truth: the "scoped" interface edit broke the build
    assert "CS0535" in s.build_errors  # the implementer no longer satisfies the interface


def test_real_cycle_is_load_bearing_for_promotion(compiler_outcome_state):
    s = compiler_outcome_state
    # 99 seeds alone are one short of MIN_CALIBRATION_SAMPLES, so promotion must NOT fire...
    assert s.promoted_pre is False
    # ...and only after the REAL compiler cycle's outcome is recorded (the 100th row) does it fire.
    assert s.promotion["risk"]["promoted"] is True, s.promotion["risk"]["veto_reasons"]
    assert s.promotion["risk"]["snapshot_id"]
    assert s.observed_risk_rows == s.seeded_cycles + s.real_build_cycles
    assert s.observed_risk_rows == 100
    assert s.real_build_cycles == 1 and s.seeded_cycles == 99


def test_learning_shifts_the_next_decision(compiler_outcome_state):
    s = compiler_outcome_state
    # The RAU shift below reflects the full 100-row calibration; the single real compiler cycle is what
    # crosses the promotion gate (asserted above), the seeds supply the volume. Honest framing: this
    # shows the learning loop ingests real compiler truth end to end — not a calibration-quality claim.
    assert s.baseline_decision == "proceed"  # cold-start green for this fixture
    assert s.learned_rau < s.baseline_rau
    assert s.learned_decision != "proceed"  # the follow-up public-API edit is now treated cautiously
    assert s.applied_snapshot_id is not None
    assert s.promotion["risk"]["snapshot_id"] in s.applied_snapshot_id.split("+")
