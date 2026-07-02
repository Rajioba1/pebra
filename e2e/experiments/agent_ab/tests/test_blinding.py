from __future__ import annotations

from e2e.experiments.agent_ab.metrics import blinding


def test_flags_experiment_word():
    leaked, matched = blinding.scan_text("Note: this is an experiment run.")
    assert leaked and "experiment" in matched


def test_flags_pebra_and_ab_and_phrase():
    assert blinding.scan_text("we call PEBRA here")[0]
    assert blinding.scan_text("the A/B split")[0]
    assert "control arm" in blinding.scan_text("you are the control arm")[1]


def test_word_boundary_does_not_false_positive():
    # "industrial" contains the substring "trial" but not the whole word.
    leaked, matched = blinding.scan_text("this is industrial-grade code")
    assert not leaked and matched == ()


def test_flags_oracle_and_group_phrase():
    assert "oracle" in blinding.scan_text("compare against the oracle")[1]
    assert "treatment group" in blinding.scan_text("you are in the treatment group")[1]


def test_bare_control_is_not_flagged_ui_domain_word():
    # UI codebase: "control" appears constantly (UserControl, "the control"); scanning transcripts for the
    # bare word would false-exclude nearly every run. Only the arm PHRASES are leaks.
    leaked, _ = blinding.scan_text("bind the user control to the view model")
    assert not leaked


def test_scan_transcript_aggregates():
    leaked, matched = blinding.scan_transcript(["clean line", "hidden treatment arm note"])
    assert leaked and "treatment arm" in matched


def test_clean_transcript_not_flagged():
    leaked, matched = blinding.scan_transcript(["add a parameter", "run the build", "done"])
    assert not leaked and matched == ()
