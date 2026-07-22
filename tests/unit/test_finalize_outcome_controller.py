from __future__ import annotations

from types import SimpleNamespace

import pytest

from pebra.app import finalize_outcome_controller as foc
from pebra.ports.learning_port import MeasurementAlreadyRecordedError


class _Store:
    def __init__(self) -> None:
        self.outcomes = []
        self.measured = False

    def load_outcomes(self, _assessment_id):
        return list(self.outcomes)

    def latest_guardrails(self, _assessment_id):
        return {"pre_commit_decision": "proceed"}

    def record_outcome(self, assessment_id, status, detail=None):
        self.outcomes.append({
            "assessment_id": assessment_id, "terminal_status": status, "detail": detail,
        })

    def prediction_errors_exist(self, _assessment_id):
        return self.measured

    def assessment_detail(self, _assessment_id):
        return {"content": {"repo_id": "r"}, "guardrails": [], "outcomes": self.outcomes}


def _result(promoted=False):
    return SimpleNamespace(promoted=promoted, snapshot_id="rs_1" if promoted else None)


class _NoVerifyStore(_Store):
    def latest_guardrails(self, _assessment_id):
        return None  # no passing pebra verify result was persisted


class _RejectedVerifyStore(_Store):
    def latest_guardrails(self, _assessment_id):
        return {"pre_commit_decision": "reject"}  # verify ran but did not pass


@pytest.mark.parametrize("store_factory", [_NoVerifyStore, _RejectedVerifyStore])
def test_completed_requires_persisted_passing_verify_trust_gate(monkeypatch, store_factory) -> None:
    """Milestone 0 characterization lock of the plan's core trust invariant.

    A 'completed' outcome cannot be finalized — and therefore can never seed a learning_context
    lesson in Milestone 5 — unless PEBRA's own verify guardrail independently persisted
    ``pre_commit_decision == 'proceed'``. Trust comes from the persisted verification, not from the
    caller identity or ``label_source``. This locks the gate at the finalize path (which routes
    through ``record_outcome_controller.record_outcome``) before any later milestone materializes a
    lesson from a completed outcome."""
    monkeypatch.setattr(
        foc.learning_controller, "measure_learning",
        lambda *a, **k: SimpleNamespace(observed=1, censored=0),
    )
    monkeypatch.setattr(foc.promotion_controller, "run_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_benefit_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_review_cost_promotion", lambda *a, **k: _result())

    store = store_factory()
    with pytest.raises(ValueError):
        foc.finalize_outcome(
            "asm_1", "completed", detail={"actual_success": True}, store=store,
            learning_port=object(),
        )
    # Fail-closed: no outcome row was written when the verify gate rejected.
    assert store.outcomes == []


def test_finalize_is_idempotent_and_uses_stable_promotion_triggers(monkeypatch) -> None:
    store = _Store()
    learning_calls = []
    promotion_calls = []

    def measure(assessment_id, **_kwargs):
        learning_calls.append(assessment_id)
        store.measured = True
        return SimpleNamespace(observed=1, censored=0)

    def promote(repo_id, **kwargs):
        promotion_calls.append((repo_id, kwargs["trigger_key"]))
        return _result()

    monkeypatch.setattr(foc.learning_controller, "measure_learning", measure)
    monkeypatch.setattr(foc.promotion_controller, "run_promotion", promote)
    monkeypatch.setattr(foc.promotion_controller, "run_benefit_promotion", promote)
    monkeypatch.setattr(foc.promotion_controller, "run_review_cost_promotion", promote)

    first = foc.finalize_outcome(
        "asm_1", "completed", detail={"actual_success": True}, store=store,
        learning_port=object(),
    )
    second = foc.finalize_outcome(
        "asm_1", "completed", detail={"actual_success": True}, store=store,
        learning_port=object(),
    )

    assert first.outcome_recorded is True and second.outcome_recorded is False
    assert first.measurement_recorded is True and second.measurement_recorded is False
    assert learning_calls == ["asm_1"]
    assert len(store.outcomes) == 1
    assert {key for _, key in promotion_calls} == {
        "finalize:asm_1:risk", "finalize:asm_1:benefit", "finalize:asm_1:review_cost",
    }


def test_finalize_rejects_conflicting_retry(monkeypatch) -> None:
    store = _Store()
    def measure(*_args, **_kwargs):
        store.measured = True
        return SimpleNamespace(observed=1, censored=0)

    monkeypatch.setattr(foc.learning_controller, "measure_learning", measure)
    monkeypatch.setattr(foc.promotion_controller, "run_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_benefit_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_review_cost_promotion", lambda *a, **k: _result())
    foc.finalize_outcome(
        "asm_1", "skipped", detail={"actual_success": False}, store=store,
        learning_port=object(),
    )
    with pytest.raises(ValueError, match="conflicts"):
        foc.finalize_outcome(
            "asm_1", "rejected", detail={"actual_success": False}, store=store,
            learning_port=object(),
        )


def test_finalize_accepts_matching_legacy_host_outcome_without_source(monkeypatch) -> None:
    store = _Store()
    store.outcomes.append({
        "assessment_id": "asm_1", "terminal_status": "skipped",
        "detail": {"actual_success": False},
    })
    store.measured = True
    monkeypatch.setattr(foc.promotion_controller, "run_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_benefit_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_review_cost_promotion", lambda *a, **k: _result())
    outcome = foc.finalize_outcome(
        "asm_1", "skipped", detail={"actual_success": False}, store=store,
        learning_port=object(),
    )
    assert outcome.outcome_recorded is False


def test_finalize_treats_lost_measurement_race_as_idempotent_retry(monkeypatch) -> None:
    store = _Store()

    def raced(*_args, **_kwargs):
        raise MeasurementAlreadyRecordedError("already measured")

    monkeypatch.setattr(foc.learning_controller, "measure_learning", raced)
    monkeypatch.setattr(foc.promotion_controller, "run_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_benefit_promotion", lambda *a, **k: _result())
    monkeypatch.setattr(foc.promotion_controller, "run_review_cost_promotion", lambda *a, **k: _result())

    outcome = foc.finalize_outcome(
        "asm_1", "completed", detail={"actual_success": True}, store=store,
        learning_port=object(),
    )

    assert outcome.outcome_recorded is True
    assert outcome.measurement_recorded is False
