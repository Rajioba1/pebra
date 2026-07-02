"""The run gate is fail-closed: it must raise unless all three conditions are set."""

from __future__ import annotations

import pytest

from e2e.experiments.agent_ab.runners import run_gate


def _set_all(mp):
    mp.setenv("E2E_AB_RUN", "1")
    mp.setenv("E2E_EXTERNAL", "1")
    mp.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_gate_open_when_all_three_present(monkeypatch):
    _set_all(monkeypatch)
    assert run_gate._gate_open() is True
    run_gate.check_gate()  # does not raise


def test_missing_ab_run_raises(monkeypatch):
    _set_all(monkeypatch)
    monkeypatch.delenv("E2E_AB_RUN")
    with pytest.raises(run_gate.RunGateError, match="E2E_AB_RUN"):
        run_gate.check_gate()


def test_missing_external_raises(monkeypatch):
    _set_all(monkeypatch)
    monkeypatch.delenv("E2E_EXTERNAL")
    with pytest.raises(run_gate.RunGateError, match="E2E_EXTERNAL"):
        run_gate.check_gate()


def test_empty_api_key_raises(monkeypatch):
    _set_all(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(run_gate.RunGateError, match="ANTHROPIC_API_KEY"):
        run_gate.check_gate()
    assert run_gate._gate_open() is False


def test_gate_closed_by_default(monkeypatch):
    monkeypatch.delenv("E2E_AB_RUN", raising=False)
    monkeypatch.delenv("E2E_EXTERNAL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert run_gate._gate_open() is False
    with pytest.raises(run_gate.RunGateError):
        run_gate.check_gate()
