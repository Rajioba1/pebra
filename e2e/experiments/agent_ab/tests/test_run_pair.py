"""Pin the fail-closed gate INSIDE the agent-invocation path. This guards the safety check across
refactors: if the check_gate() call at the top of _invoke_subject_agent were removed, these fail. Now
that Phase G has removed the AnthropicClient.send NotImplementedError stop, the gate is the SOLE guard,
so this pin is the last line of defence against an accidental live run."""

from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.models import TaskSpec
from e2e.experiments.agent_ab.runners import run_control, run_gate, run_pair

_SPEC = TaskSpec("T1", "d", ("a.cs",), "risky", ("a.cs",), "build_failure", True)


def _dummy_setup(tmp_path):
    return run_pair.ArmSetup(
        arm="control", repo_path=tmp_path, advisory_backend=lambda payload: {},
        baseline_build=None, subject_prompt="do the task",
    )


def _close_gate(mp):
    mp.delenv("E2E_AB_RUN", raising=False)
    mp.delenv("E2E_EXTERNAL", raising=False)
    mp.delenv("ANTHROPIC_API_KEY", raising=False)


def test_invoke_subject_agent_gated_fail_closed(monkeypatch, tmp_path):
    _close_gate(monkeypatch)
    # The gate is the FIRST statement; it must raise before any config load / AnthropicClient / clone.
    with pytest.raises(run_gate.RunGateError):
        run_pair._invoke_subject_agent(_dummy_setup(tmp_path), _SPEC, 0)


def test_run_control_is_gated(monkeypatch, tmp_path):
    _close_gate(monkeypatch)
    # Bypass the real external clone; the gate inside _invoke_subject_agent must still fire.
    monkeypatch.setattr(run_control.rs, "prepare_external_repo", lambda *a, **k: object())
    monkeypatch.setattr(run_control.run_pair, "prepare_arm",
                        lambda *a, **k: _dummy_setup(tmp_path))
    with pytest.raises(run_gate.RunGateError):
        run_control.run_control(_SPEC, 0, "rid")
