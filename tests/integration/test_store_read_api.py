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
    candidate_verification_status: str | None = None,
    candidate_verification_retryable: bool = False,
    graph_refinement_status: str | None = None,
    graph_retryable: bool = False,
    with_revision_envelope: bool = True,
    baseline_binding: dict | None = None,
    lineage_key: str | None = None,
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
    if candidate_verification_status is not None:
        request["candidate_verification_status"] = candidate_verification_status
        request["candidate_verification_retryable_infrastructure"] = (
            candidate_verification_retryable
        )
    if graph_refinement_status is not None:
        request["graph_refinement"] = {
            "status": graph_refinement_status,
            "retryable_infrastructure": graph_retryable,
        }
    if with_revision_envelope:
        request["revision_envelope"] = {
            "expected_files": [
                value for value in (files or ["src/Gamma.cs"]) if "::" not in value
            ],
            "public_symbols": [],
            "expected_loss": 0.36,
            "rau": -0.12,
            "baseline_binding": baseline_binding or {
                "algorithm": "sha256-git-worktree-v1",
                "digest": "base",
            },
            "lineage_key": lineage_key or "lineage:" + ",".join(sorted(
                value.replace("\\", "/").removeprefix("./")
                for value in (files or ["src/Gamma.cs"])
                if "::" not in value
            )),
        }
    return store.persist_assessment(res, request)


def test_revise_safer_attempt_count_matches_repo_head_and_safe_scope(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["src/Gamma.cs", "src/Gamma.cs::Gamma"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["./src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, commit="other", files=["src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.PROCEED, files=["src/Gamma.cs"])
    _persist_scoped(store, decision=Decision.REVISE_SAFER, files=["src/Other.cs"])

    assert store.revise_safer_attempt_count("r", "abc123", ["src/Gamma.cs"]) == 2


def test_revise_safer_attempt_count_ignores_caller_action_and_task_labels(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/LowImpact.cs"],
        action_id="first-label",
    )
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/LowImpact.cs"],
        action_id="changed-label",
        task="different task",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/LowImpact.cs"], "new-label", "new task",
        {"algorithm": "sha256-git-worktree-v1", "digest": "base"},
    ) == 2


def test_unclassified_unavailable_verification_charges_revision_attempt(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(store, decision=Decision.REVISE_SAFER)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        candidate_verification_status="unavailable",
    )
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        candidate_verification_status="failed",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 3


def test_retryable_candidate_verification_infrastructure_does_not_charge_attempt(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        candidate_verification_status="unavailable",
        candidate_verification_retryable=True,
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 0


def test_repeated_retryable_infrastructure_failure_eventually_charges_attempt(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    for _ in range(2):
        _persist_scoped(
            store,
            decision=Decision.REVISE_SAFER,
            candidate_verification_status="unavailable",
            candidate_verification_retryable=True,
        )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 1


def test_candidate_invalid_graph_unavailability_charges_revision_attempt(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        graph_refinement_status="unavailable",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 1


def test_retryable_graph_infrastructure_failure_does_not_charge_attempt(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        graph_refinement_status="unavailable",
        graph_retryable=True,
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 0


def test_retryable_graph_failure_cannot_mask_completed_verification_failure(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        candidate_verification_status="failed",
        graph_refinement_status="unavailable",
        graph_retryable=True,
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Gamma.cs"], "action-1", "t"
    ) == 1


def test_revision_origin_envelope_returns_first_matching_action_at_head(tmp_path) -> None:
    store = _store(tmp_path)
    first = {
        "expected_files": ["src/api.ts", "src/compat.ts"],
        "public_symbols": ["pkg.oldName"],
        "expected_loss": 0.36,
        "benefit": 0.52,
        "expected_utility": 0.03,
        "utility_sd": 0.1171875,
        "rau": -0.12,
        "baseline_binding": {"algorithm": "sha256-git-worktree-v1", "digest": "base"},
        "lineage_key": "lineage-first",
    }
    second = {
        "expected_files": ["src/api.ts"],
        "public_symbols": [],
        "expected_loss": 0.18,
        "benefit": 0.58,
        "expected_utility": 0.24,
        "utility_sd": 0.03125,
        "rau": 0.20,
        "baseline_binding": {"algorithm": "sha256-git-worktree-v1", "digest": "base"},
        "lineage_key": "lineage-first",
    }
    for envelope in (first, second):
        res = AssessmentResult(
            recommended_decision=Decision.REVISE_SAFER,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.SENSITIVE_CONTEXT,
            scores={},
            repo_id="r",
            repo_root="/x",
            assessed_commit="abc123",
        )
        store.persist_assessment(
            res,
            {"task": "rename", "action_id": "a1", "revision_envelope": envelope},
        )

    origin = store.revision_origin_envelope(
        "r", "abc123", "changed-id", "changed task",
        ["src/api.ts", "src/compat.ts"],
        {"algorithm": "sha256-git-worktree-v1", "digest": "base"},
    )
    assert origin == {
        "available": True,
        "assessment_id": "asm_1",
        **first,
    }


def test_revision_origin_envelope_marks_matching_legacy_row_unavailable(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        action_id="legacy",
        task="rename",
        with_revision_envelope=False,
    )

    assert store.revision_origin_envelope(
        "r", "abc123", "legacy", "rename", ["src/Gamma.cs"]
    ) == {
        "available": False,
        "fallback_reason": "origin assessment predates structural lineage binding",
    }


def test_changed_action_id_does_not_inherit_revision_lineage(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts", "src/compat.ts"],
        action_id="original-action",
        task="rename safely",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/api.ts"], "new-action", "rename safely"
    ) == 0


def test_expanded_candidate_envelope_keeps_structural_origin(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = {"algorithm": "sha256-git-worktree-v1", "digest": "base"}
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts"],
        action_id="origin-label",
        task="old wording",
        baseline_binding=baseline,
        lineage_key="lineage-expand",
    )

    origin = store.revision_origin_envelope(
        "r", "abc123", "changed-label", "new wording",
        ["src/api.ts", "src/compat.ts"], baseline,
    )

    assert origin is not None
    assert origin["available"] is True
    assert origin["expected_files"] == ["src/api.ts"]
    assert origin["lineage_key"] == "lineage-expand"


def test_dropped_file_candidate_keeps_overlapping_structural_origin(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = {"algorithm": "sha256-git-worktree-v1", "digest": "base"}
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts", "src/compat.ts"],
        baseline_binding=baseline,
        lineage_key="lineage-contract",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/api.ts"], baseline_binding=baseline
    ) == 1
    origin = store.revision_origin_envelope(
        "r", "abc123", "changed-label", "changed wording", ["src/api.ts"], baseline
    )
    assert origin is not None
    assert origin["available"] is True
    assert origin["expected_files"] == ["src/api.ts", "src/compat.ts"]


def test_disjoint_candidate_does_not_inherit_structural_origin(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = {"algorithm": "sha256-git-worktree-v1", "digest": "base"}
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts", "src/compat.ts"],
        baseline_binding=baseline,
        lineage_key="lineage-contract",
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/unrelated.ts"], baseline_binding=baseline
    ) == 0
    assert store.revision_origin_envelope(
        "r", "abc123", "changed-label", "changed wording", ["src/unrelated.ts"], baseline
    ) is None


def test_ambiguous_overlapping_origins_fail_closed(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = {"algorithm": "sha256-git-worktree-v1", "digest": "base"}
    for files, lineage in (
        (["src/api.ts"], "lineage-api"),
        (["src/api.ts", "src/compat.ts"], "lineage-contract"),
    ):
        _persist_scoped(
            store,
            decision=Decision.REVISE_SAFER,
            files=files,
            baseline_binding=baseline,
            lineage_key=lineage,
        )

    assert store.revision_origin_envelope(
        "r", "abc123", "candidate", "changed wording", ["src/api.ts"], baseline
    ) == {
        "available": False,
        "fallback_reason": "multiple overlapping revision origins match this candidate",
    }


def test_attempt_count_uses_most_advanced_overlapping_lineage(tmp_path) -> None:
    store = _store(tmp_path)
    baseline = {"algorithm": "sha256-git-worktree-v1", "digest": "base"}
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts"],
        baseline_binding=baseline,
        lineage_key="lineage-one",
    )
    for _ in range(2):
        _persist_scoped(
            store,
            decision=Decision.REVISE_SAFER,
            files=["src/api.ts", "src/compat.ts"],
            baseline_binding=baseline,
            lineage_key="lineage-two",
        )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/api.ts"], baseline_binding=baseline
    ) == 2


def test_origin_envelope_does_not_cross_modern_request_lineage(tmp_path) -> None:
    store = _store(tmp_path)
    envelope = {
        "expected_files": ["src/api.ts", "src/compat.ts"],
        "public_symbols": ["pkg.oldName"],
        "expected_loss": 0.36,
        "rau": -0.12,
    }
    res = AssessmentResult(
        recommended_decision=Decision.REVISE_SAFER,
        requires_confirmation=False,
        action_status=ActionStatus.PENDING,
        risk_mode=RiskMode.SENSITIVE_CONTEXT,
        scores={},
        repo_id="r",
        repo_root="/x",
        assessed_commit="abc123",
    )
    store.persist_assessment(
        res,
        {"task": "old wording", "action_id": "old-action", "revision_envelope": envelope},
    )

    assert store.revision_origin_envelope(
        "r", "abc123", "new-action", "new wording", ["src/api.ts"]
    ) is None


def test_origin_envelope_legacy_scope_match_is_unavailable(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/api.ts"],
        action_id=None,
        task="rename safely",
        with_revision_envelope=False,
    )

    assert store.revision_origin_envelope(
        "r", "abc123", "new-action", "rename safely", ["src/api.ts"]
    ) == {
        "available": False,
        "fallback_reason": "origin assessment predates structural lineage binding",
    }


def test_legacy_scope_fallback_cannot_be_reset_by_changing_task_text(tmp_path) -> None:
    store = _store(tmp_path)
    _persist_scoped(
        store,
        decision=Decision.REVISE_SAFER,
        files=["src/Same.cs"],
        action_id=None,
        task="different task",
        with_revision_envelope=False,
    )

    assert store.revise_safer_attempt_count(
        "r", "abc123", ["src/Same.cs"], "new-action", "current task"
    ) == 1


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
