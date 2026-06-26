"""Phase 3b (5c-A) — read-only store API the Risk Observatory dashboard queries.

These power the dashboard panels without going through app/core: list (overview + history),
assessment_detail (decision/guidance/architecture/outcomes), and chain_status (audit panel).
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(str(tmp_path / "pebra.db"))


def _persist(store: SqliteStore, *, repo_id: str = "r", decision: Decision = Decision.PROCEED) -> str:
    res = AssessmentResult(
        recommended_decision=decision,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"edit_confidence": 0.83, "risk_budget_used": 0.5},
        repo_id=repo_id,
        repo_root="/x",
        model_guidance_packet={"decision": decision.value, "advisory": {"why": ["because"]}},
    )
    return store.persist_assessment(res, {"task": "t"})


def test_list_assessments_newest_first_scoped_to_repo(tmp_path) -> None:
    store = _store(tmp_path)
    a = _persist(store, repo_id="r1")
    b = _persist(store, repo_id="r1")
    _persist(store, repo_id="other")
    rows = store.list_assessments("r1")
    assert [r["assessment_id"] for r in rows] == [b, a]  # DESC by id
    assert rows[0]["decision"] == "proceed"
    assert rows[0]["scores"]["edit_confidence"] == 0.83


def test_list_assessments_pagination(tmp_path) -> None:
    store = _store(tmp_path)
    ids = [_persist(store, repo_id="r") for _ in range(3)]
    page = store.list_assessments("r", limit=1, offset=1)
    assert len(page) == 1
    assert page[0]["assessment_id"] == ids[1]  # middle row (newest-first, offset 1)


def test_list_assessments_exposes_terminal_status(tmp_path) -> None:
    store = _store(tmp_path)
    done = _persist(store, repo_id="r")
    pending = _persist(store, repo_id="r")
    store.record_outcome(done, "completed", {"x": 1})
    by_id = {r["assessment_id"]: r for r in store.list_assessments("r")}
    assert by_id[done]["terminal_status"] == "completed"
    assert by_id[done]["outcome_recorded_at"] is not None
    assert by_id[pending]["terminal_status"] is None  # no outcome yet -> pending


def test_list_assessments_clamps_negative_limit(tmp_path) -> None:
    store = _store(tmp_path)
    for _ in range(3):
        _persist(store, repo_id="r")
    # a negative LIMIT is unbounded in SQLite; it must be clamped, not dump every row
    assert store.list_assessments("r", limit=-1) == []


def test_assessment_detail_joins_guidance_and_outcomes(tmp_path) -> None:
    store = _store(tmp_path)
    asm = _persist(store)
    store.record_outcome(asm, "completed", {"actual": "ok"})
    detail = store.assessment_detail(asm)
    assert detail["assessment_id"] == asm
    assert detail["content"]["decision"] == "proceed"
    assert detail["model_guidance_packet"]["advisory"]["why"] == ["because"]
    assert detail["outcomes"][0]["terminal_status"] == "completed"


def test_assessment_detail_unknown_raises(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.assessment_detail("asm_999")


def test_chain_status_reports_valid_and_counts(tmp_path) -> None:
    store = _store(tmp_path)
    asm = _persist(store)
    store.record_outcome(asm, "completed")
    status = store.chain_status()
    assert status["valid"] is True
    assert status["counts"]["assessments"] == 1
    assert status["counts"]["outcomes"] == 1
