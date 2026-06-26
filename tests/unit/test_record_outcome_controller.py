"""Phase 3a — record_outcome_controller: only terminal statuses close the lifecycle (AD-4)."""

from __future__ import annotations

import pytest

from pebra.app import record_outcome_controller as roc


class _FakeOutcome:
    def __init__(self, guardrails=None) -> None:
        self.calls: list[tuple] = []
        self._guardrails = guardrails

    def record_outcome(self, assessment_id: str, status: str, detail=None) -> None:
        self.calls.append((assessment_id, status, detail))

    def latest_guardrails(self, assessment_id: str):
        return self._guardrails


def test_terminal_status_is_recorded() -> None:
    fake = _FakeOutcome({"pre_commit_decision": "proceed"})
    roc.record_outcome("asm_1", "completed", outcome_port=fake, detail={"x": 1})
    assert fake.calls == [("asm_1", "completed", {"x": 1})]


@pytest.mark.parametrize("status", ["completed", "skipped", "rejected"])
def test_all_terminal_statuses_accepted(status) -> None:
    fake = _FakeOutcome({"pre_commit_decision": "proceed"})
    roc.record_outcome("asm_1", status, outcome_port=fake)
    assert fake.calls[0][1] == status


@pytest.mark.parametrize("bad", ["pending", "bogus", "", "Completed"])
def test_non_terminal_status_rejected(bad) -> None:
    fake = _FakeOutcome()
    with pytest.raises(ValueError, match="terminal"):
        roc.record_outcome("asm_1", bad, outcome_port=fake)
    assert fake.calls == []  # nothing written when the status is invalid


def test_completed_requires_latest_passing_verify() -> None:
    fake = _FakeOutcome()
    with pytest.raises(ValueError, match="requires a latest passing"):
        roc.record_outcome("asm_1", "completed", outcome_port=fake)
    assert fake.calls == []

    fake = _FakeOutcome({"pre_commit_decision": "test_first"})
    with pytest.raises(ValueError, match="pre_commit_decision='proceed'"):
        roc.record_outcome("asm_1", "completed", outcome_port=fake)
    assert fake.calls == []


def test_skipped_and_rejected_do_not_require_verify() -> None:
    fake = _FakeOutcome()
    roc.record_outcome("asm_1", "skipped", outcome_port=fake)
    roc.record_outcome("asm_2", "rejected", outcome_port=fake)
    assert [c[1] for c in fake.calls] == ["skipped", "rejected"]
