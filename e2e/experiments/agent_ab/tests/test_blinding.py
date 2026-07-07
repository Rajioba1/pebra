from __future__ import annotations

from pathlib import Path

import pytest

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.metrics import blinding
from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import run_pair

_SPEC = TaskSpec("MNGAMMA", "Refactor the duplicated loops.", ("src/Gamma.cs",), "risky",
                 ("src/Gamma.cs",), "test_failure", False)


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


@pytest.mark.parametrize(
    "arm",
    [models.ARM_SHAM, models.ARM_ORACLE_POSITIVE, models.ARM_ENFORCED_CONTROL,
     models.ARM_BLAST_RADIUS, models.ARM_PEBRA],
)
def test_harness_authored_prompts_do_not_leak_arm_identity(arm):
    prompt = run_pair._build_subject_prompt(_SPEC, Path("opaque"), arm)
    leaked, matched = blinding.scan_text(prompt)
    assert not leaked, matched


@pytest.mark.parametrize(
    "arm",
    [models.ARM_SHAM, models.ARM_ORACLE_POSITIVE, models.ARM_ENFORCED_CONTROL,
     models.ARM_BLAST_RADIUS, models.ARM_PEBRA],
)
def test_gate_denial_reasons_do_not_leak_arm_identity(arm, tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_pair.cli_harness,
        "gate_check",
        lambda event, *, db, consult_only: {
            "permission": "deny",
            "reason": "A pre-edit check blocked this write. Revise or stop.",
        },
    )
    decision = run_pair._gate_check_backend(arm, tmp_path / "pebra.db")({})
    reason = str(decision.get("reason") or "")
    leaked, matched = blinding.scan_text(reason)
    assert not leaked, matched


def test_enforced_control_reason_matches_real_gate_shape(tmp_path, monkeypatch):
    seen = {}

    class Proc:
        returncode = 0
        stdout = "abcdef1234567890\n"
        stderr = ""

    def _run(args, **kwargs):
        seen["args"] = args
        seen["cwd"] = kwargs["cwd"]
        return Proc()

    monkeypatch.setattr(run_pair.subprocess, "run", _run)
    backend = run_pair._gate_check_backend(models.ARM_ENFORCED_CONTROL, tmp_path / "pebra.db")
    decision = backend({
        "tool_name": "Write",
        "tool_input": {"file_path": "src/Gamma.cs"},
        "cwd": str(tmp_path),
    })

    reason = str(decision.get("reason") or "")
    assert "Gamma.cs" in reason
    assert "abcdef12" in reason
    leaked, matched = blinding.scan_text(reason)
    assert not leaked, matched
