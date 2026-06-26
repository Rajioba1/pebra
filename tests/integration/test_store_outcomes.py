"""Phase 3a — outcomes table + record_outcome (AD-4 terminal status), own append-only hash chain.

The assessment row is immutable (hash-chained content_json), so closing the action_status lifecycle
appends an outcome row rather than mutating the assessment. Current status is derived as the recorded
outcome's terminal_status, else pending.
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(str(tmp_path / "pebra.db"))


def _assessment(store: SqliteStore) -> str:
    res = AssessmentResult(
        recommended_decision=Decision.PROCEED,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.NORMAL,
        scores={},
        repo_id="r",
        repo_root="/x",
    )
    return store.persist_assessment(res, {"task": "t"})


def test_record_outcome_appends_and_chain_stays_valid(tmp_path) -> None:
    store = _store(tmp_path)
    asm = _assessment(store)
    store.record_outcome(asm, "completed", {"actual": "ok"})
    assert store.validate_chain() is True
    outs = store.load_outcomes(asm)
    assert len(outs) == 1
    assert outs[0]["terminal_status"] == "completed"
    assert outs[0]["detail"] == {"actual": "ok"}


def test_record_outcome_unknown_assessment_raises(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.record_outcome("asm_999", "completed")


def test_outcome_chain_detects_tamper(tmp_path) -> None:
    store = _store(tmp_path)
    asm = _assessment(store)
    store.record_outcome(asm, "completed")
    store._con.execute("UPDATE outcomes SET detail_json = ? WHERE id = 1", ('{"tampered":true}',))
    store._con.commit()
    assert store.validate_chain() is False


def test_second_outcome_is_rejected(tmp_path) -> None:
    # AD-4: the lifecycle closes exactly once — a second (possibly contradictory) outcome is refused.
    store = _store(tmp_path)
    asm = _assessment(store)
    store.record_outcome(asm, "completed")
    with pytest.raises(ValueError):
        store.record_outcome(asm, "skipped")
    assert store.validate_chain() is True
    assert len(store.load_outcomes(asm)) == 1  # only the first outcome stands


def test_no_outcomes_chain_valid(tmp_path) -> None:
    store = _store(tmp_path)
    _assessment(store)
    assert store.validate_chain() is True  # empty outcome chain doesn't break validation
