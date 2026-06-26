"""Phase 3a — record_outcome_controller: only terminal statuses close the lifecycle (AD-4)."""

from __future__ import annotations

import pytest

from pebra.app import record_outcome_controller as roc


class _FakeOutcome:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record_outcome(self, assessment_id: str, status: str, detail=None) -> None:
        self.calls.append((assessment_id, status, detail))


def test_terminal_status_is_recorded() -> None:
    fake = _FakeOutcome()
    roc.record_outcome("asm_1", "completed", outcome_port=fake, detail={"x": 1})
    assert fake.calls == [("asm_1", "completed", {"x": 1})]


@pytest.mark.parametrize("status", ["completed", "skipped", "rejected"])
def test_all_terminal_statuses_accepted(status) -> None:
    fake = _FakeOutcome()
    roc.record_outcome("asm_1", status, outcome_port=fake)
    assert fake.calls[0][1] == status


@pytest.mark.parametrize("bad", ["pending", "bogus", "", "Completed"])
def test_non_terminal_status_rejected(bad) -> None:
    fake = _FakeOutcome()
    with pytest.raises(ValueError, match="terminal"):
        roc.record_outcome("asm_1", bad, outcome_port=fake)
    assert fake.calls == []  # nothing written when the status is invalid
