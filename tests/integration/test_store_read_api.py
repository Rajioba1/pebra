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


def _persist_scoped(
    store: SqliteStore,
    *,
    repo_id: str = "r",
    decision: Decision = Decision.PROCEED,
    commit: str = "abc123",
    files: list[str] | None = None,
    action_id: str | None = "action-1",
    task: str = "t",
) -> str:
    res = AssessmentResult(
        recommended_decision=decision,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={"edit_confidence": 0.83, "risk_budget_used": 0.5},
        repo_id=repo_id,
        repo_root="/x",
        assessed_commit=commit,
        model_guidance_packet={
            "decision": decision.value,
            "binding": {"safe_scope": {"files": files or ["src/Gamma.cs"]}},
            "advisory": {"why": ["because"]},
        },
    )
    request = {"task": task}
    if action_id is not None:
        request["action_id"] = action_id
    return store.persist_assessment(res, request)


def test_revise_safer_attempt_count_matches_repo_head_and_safe_scope(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["src/Gamma.cs", "src/Gamma.cs::Gamma"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["./src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, commit="other", files=["src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.PROCEED, files=["src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["src/Other.cs"])

    assert store.revise_safer_attempt_count("r", "abc123", ["src/Gamma.cs"]) == 2


def test_revise_safer_attempt_count_follows_action_across_files(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/HighImpact.cs"],
        action_id="stable-revision-lineage",
    )
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/LowImpact.cs"],
        action_id="other-action",
    )
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/LowImpact.cs"],
        action_id="stable-revision-lineage",
        task="different task",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/LowImpact.cs"], "stable-revision-lineage", "t"
    ) == 1


def test_legacy_scope_fallback_does_not_cross_known_task_boundary(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/Same.cs"],
        action_id=None,
        task="different task",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Same.cs"], "new-action", "current task"
    ) == 0


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


# --- calibration + learning read surface (dashboard views) ---


def _pe_row(
    *, target_type: str = "risk_binary", predicted: float = 0.7, actual: int = 1, production: bool = True
) -> dict:
    return {
        "target_type": target_type,
        "target_name": "edit",
        "predicted_probability": predicted,
        "actual_outcome": actual,
        "outcome_label_status": "observed",
        "calibration_scope": "proceeded_edits_only" if production else "shadow",
        "shadow_mode": 0 if production else 1,
        "hash_version": 2,
        "benefit_guidance_influenced": 0,
    }


def test_list_prediction_errors_production_scope_is_repo_scoped_and_trusted(tmp_path) -> None:
    store = _store(tmp_path)
    a1 = _persist(store, repo_id="r1")
    store.insert_prediction_error(a1, _pe_row(predicted=0.7, actual=1))
    store.insert_prediction_error(a1, _pe_row(predicted=0.2, actual=0))
    store.insert_prediction_error(a1, _pe_row(predicted=0.9, actual=1, production=False))  # shadow
    other = _persist(store, repo_id="other")
    store.insert_prediction_error(other, _pe_row(predicted=0.5, actual=1))

    rows = store.list_prediction_errors("r1", target_type="risk_binary", scope="production")
    assert len(rows) == 2  # the shadow row and the other-repo row are both excluded
    assert {(r["predicted_probability"], r["actual_outcome"]) for r in rows} == {(0.7, 1), (0.2, 0)}


def test_list_prediction_errors_all_scope_includes_shadow(tmp_path) -> None:
    store = _store(tmp_path)
    a1 = _persist(store, repo_id="r1")
    store.insert_prediction_error(a1, _pe_row(predicted=0.7, actual=1))
    store.insert_prediction_error(a1, _pe_row(predicted=0.9, actual=1, production=False))  # shadow

    rows = store.list_prediction_errors("r1", target_type="risk_binary", scope="all")
    assert len(rows) == 2  # observed shadow rows are visible in the "all" scope


def test_list_risk_snapshots_newest_first_with_lifecycle(tmp_path) -> None:
    store = _store(tmp_path)
    store.insert_risk_snapshot("r1", {"ece": 0.1}, "shadow")
    store.insert_risk_snapshot(
        "r1", {"promotion_reason": "benefit_promoted", "drift_score": 0.2}, "active"
    )
    store.insert_risk_snapshot("other", {"ece": 0.3}, "shadow")

    rows = store.list_risk_snapshots("r1")
    assert len(rows) == 2  # scoped to r1
    assert rows[0]["status"] == "active"  # newest first
    assert rows[0]["promotion_reason"] == "benefit_promoted"
    assert rows[0]["drift_score"] == 0.2
    assert rows[1]["metrics"]["ece"] == 0.1


def test_list_learned_risk_facts_scoped_to_repo_and_snapshot(tmp_path) -> None:
    store = _store(tmp_path)
    facts = [
        {"target_type": "risk_binary", "target_name": "Gamma::LogGamma", "fact_json": {"delta": 0.1}},
        {"target_type": "risk_binary", "target_name": "Gamma::Gamma", "fact_json": {"delta": 0.2}},
    ]
    snap_id, _ = store.insert_learned_fact_batch_with_snapshot("r1", {"ece": 0.1}, facts, "active")
    store.insert_learned_fact_batch_with_snapshot(
        "other", {"ece": 0.1}, [{"target_type": "risk_binary", "target_name": "X"}], "active"
    )

    rows = store.list_learned_risk_facts("r1")
    assert len(rows) == 2
    assert {r["target_name"] for r in rows} == {"Gamma::LogGamma", "Gamma::Gamma"}
    assert rows[0]["fact"]  # fact_json parsed to a dict
    assert {r["snapshot_id"] for r in rows} == {snap_id}

    scoped = store.list_learned_risk_facts("r1", snapshot_id=snap_id)
    assert len(scoped) == 2
    assert store.list_learned_risk_facts("r1", snapshot_id="rs_99999") == []
