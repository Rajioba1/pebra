"""Phase 3a — record_outcome_controller: only terminal statuses close the lifecycle (AD-4)."""

from __future__ import annotations

import pytest

from pebra.app import record_outcome_controller as roc


class _FakeOutcome:
    def __init__(self, guardrails=None, *, materialized=object(), materialize_error=None) -> None:
        self.calls: list[tuple] = []
        self._guardrails = guardrails
        self.materialize_calls: list[str] = []
        self.materialized = materialized
        self.materialize_error = materialize_error

    def record_outcome(self, assessment_id: str, status: str, detail=None) -> None:
        self.calls.append((assessment_id, status, detail))

    def latest_guardrails(self, assessment_id: str):
        return self._guardrails

    def materialize_learning_context(self, assessment_id: str):
        self.materialize_calls.append(assessment_id)
        if self.materialize_error:
            raise self.materialize_error
        return self.materialized


def test_terminal_status_is_recorded() -> None:
    fake = _FakeOutcome({"pre_commit_decision": "proceed"})
    result = roc.record_outcome(
        "asm_1", "completed", outcome_port=fake, learning_context_port=fake, detail={"x": 1}
    )
    assert fake.calls == [("asm_1", "completed", {"x": 1, "_pebra_label_source": "host"})]
    assert fake.materialize_calls == ["asm_1"]
    assert result.outcome_recorded is True
    assert result.context_materialized is True


def test_agent_label_source_is_persisted_for_censoring() -> None:
    fake = _FakeOutcome()
    roc.record_outcome(
        "asm_1", "skipped", outcome_port=fake,
        detail={"actual_success": True}, label_source="agent",
    )
    assert fake.calls[0][2]["_pebra_label_source"] == "agent"


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


def test_non_completed_outcomes_never_materialize() -> None:
    fake = _FakeOutcome()
    result = roc.record_outcome(
        "asm_1", "skipped", outcome_port=fake, learning_context_port=fake
    )
    assert fake.materialize_calls == []
    assert result.context_materialized is False


def test_materializer_failure_reports_partial_success_without_rewriting_outcome() -> None:
    fake = _FakeOutcome(
        {"pre_commit_decision": "proceed"}, materialize_error=RuntimeError("storage failed")
    )
    result = roc.record_outcome(
        "asm_1", "completed", outcome_port=fake, learning_context_port=fake,
        detail={"lesson": "caller text must not be forwarded"},
    )
    assert len(fake.calls) == 1
    assert fake.materialize_calls == ["asm_1"]
    assert result.outcome_recorded is True
    assert result.context_materialized is False
    assert result.context_error == "RuntimeError"


def test_materializer_refusal_after_guardrail_reread_is_honest_partial_state() -> None:
    fake = _FakeOutcome({"pre_commit_decision": "proceed"}, materialized=None)
    result = roc.record_outcome(
        "asm_1", "completed", outcome_port=fake, learning_context_port=fake
    )
    assert result.outcome_recorded is True
    assert result.context_materialized is False
    assert result.context_error is None
