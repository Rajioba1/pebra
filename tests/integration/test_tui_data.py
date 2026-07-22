"""Integration tests for the ObservatoryData read facade (Observatory TUI M3).

ObservatoryData opens a short-lived read-only SqliteStore per read session, delegates to the M1 shared
query controller, and closes in finally. It shapes nothing and re-derives no decisions — it only forwards
controller output. These tests drive it against a real seeded store.
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM
from pebra.core.constants import ActionStatus, Decision, RiskMode
from pebra.core.models import AssessmentResult
from pebra.observatory_context import ObservatoryContext
from pebra.tui.data import ObservatoryData, ObservatoryStoreUnavailable


def _persist(store: SqliteStore, *, repo_id: str, decision: Decision, commit: str, scores: dict) -> str:
    return store.persist_assessment(
        AssessmentResult(
            recommended_decision=decision,
            requires_confirmation=decision is not Decision.PROCEED,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores=scores,
            repo_id=repo_id,
            repo_root="/x",
            model_guidance_packet={"decision": decision.value},
            assessed_commit=commit,
        ),
        {"task": "t"},
    )


def _seed(tmp_path) -> tuple[str, str, str]:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    asm = _persist(
        store, repo_id="r", decision=Decision.ASK_HUMAN, commit="abc1234",
        scores={"rau": -0.14, "benefit": 0.53, "expected_loss": 0.15,
                "expected_utility": 0.01, "edit_confidence": 0.7},
    )
    _persist(
        store, repo_id="r", decision=Decision.PROCEED, commit="def5678",
        scores={"rau": 0.14, "benefit": 0.52, "expected_loss": 0.05,
                "expected_utility": 0.21, "edit_confidence": 0.83},
    )
    foreign = _persist(
        store, repo_id="other", decision=Decision.REJECT, commit="999aaaa",
        scores={"rau": -0.31, "benefit": 0.20, "expected_loss": 0.36},
    )
    store.close()
    return db, asm, foreign


def _ctx(db: str, repo_id: str = "r") -> ObservatoryContext:
    return ObservatoryContext(db_path=db, repo_id=repo_id, repo_root=None, read_only=True)


def test_refresh_snapshot_returns_overview_rows_series_and_chain(tmp_path) -> None:
    db, _, _ = _seed(tmp_path)
    snap = ObservatoryData(_ctx(db)).refresh_snapshot()

    assert snap.overview["total"] == 2  # repo "r" only; the foreign row is excluded
    assert snap.overview["by_decision"] == {"ask_human": 1, "proceed": 1}
    assert snap.chain["valid"] is True
    # newest-first; the proceed row was persisted last
    assert snap.assessments[0]["decision"] == "proceed"
    assert {r["decision"] for r in snap.assessments} == {"proceed", "ask_human"}
    series_ids = {item["assessment_id"] for item in snap.scores_series}
    assert series_ids == {r["assessment_id"] for r in snap.assessments}
    assert snap.scores_series[0]["scores"]["rau"] == 0.14


def test_refresh_snapshot_exposes_projected_assessment_identity(tmp_path) -> None:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={
                "binding": {
                    "candidate": {
                        "algorithm": CANDIDATE_BINDING_ALGORITHM,
                        "files": {"src/auth.py": "a" * 64},
                    }
                }
            },
        ),
        {
            "task": "Fix authentication",
            "action_id": "edit-auth",
            "revision_envelope": {"expected_files": ["src/auth.py"]},
        },
    )
    store.close()

    row = ObservatoryData(_ctx(db)).refresh_snapshot().assessments[0]

    assert row["task"] == "Fix authentication"
    assert row["action_id"] == "edit-auth"
    assert row["target_files"] == ["src/auth.py"]
    assert row["target_provenance"] == "candidate_bound"
    assert len(row["candidate_fingerprint"]) == 64


def test_detail_is_repo_scoped(tmp_path) -> None:
    db, asm, _ = _seed(tmp_path)
    detail = ObservatoryData(_ctx(db)).detail(asm)
    assert detail["content"]["repo_id"] == "r"


def test_detail_rejects_foreign_repo(tmp_path) -> None:
    from pebra.app.observatory_query_controller import AssessmentNotFoundError

    db, _, foreign = _seed(tmp_path)
    with pytest.raises(AssessmentNotFoundError):
        ObservatoryData(_ctx(db)).detail(foreign)


def test_unavailable_store_raises(tmp_path) -> None:
    data = ObservatoryData(_ctx(str(tmp_path / "does-not-exist.db")))
    with pytest.raises(ObservatoryStoreUnavailable):
        data.refresh_snapshot()


def test_query_failure_is_translated_and_session_is_closed(monkeypatch, tmp_path) -> None:
    db = tmp_path / "schema-less.db"
    db.touch()  # Opens read-only, then fails on the first assessment query.
    import pebra.tui.data as data_mod

    real = data_mod.SqliteStore
    closes: list[bool] = []

    class _Spy(real):  # type: ignore[valid-type,misc]
        def close(self):
            closes.append(True)
            super().close()

    monkeypatch.setattr(data_mod, "SqliteStore", _Spy)

    with pytest.raises(ObservatoryStoreUnavailable):
        ObservatoryData(_ctx(str(db))).refresh_snapshot()
    assert closes == [True]


def test_refresh_opens_exactly_one_readonly_session(monkeypatch, tmp_path) -> None:
    db, _, _ = _seed(tmp_path)
    import pebra.tui.data as data_mod

    real = data_mod.SqliteStore
    opens: list[bool] = []
    closes: list[bool] = []

    class _Spy(real):  # type: ignore[valid-type,misc]
        def __init__(self, path, *, read_only=False):
            opens.append(read_only)
            super().__init__(path, read_only=read_only)

        def close(self):
            closes.append(True)
            super().close()

    monkeypatch.setattr(data_mod, "SqliteStore", _Spy)

    ObservatoryData(_ctx(db)).refresh_snapshot()

    assert opens == [True]  # exactly one open, read-only (strict no-write)
    assert len(closes) == 1  # closed once, in finally


def test_refresh_snapshot_includes_visible_row_prior_facets_but_not_full_learning_tables(tmp_path) -> None:
    db, _, _ = _seed(tmp_path)
    snapshot = ObservatoryData(_ctx(db)).refresh_snapshot()

    visible_ids = {row["assessment_id"] for row in snapshot.assessments}
    assert set(snapshot.prior_facets) == visible_ids
    assert {facet["source"] for facet in snapshot.prior_facets.values()} == {"cold_start"}


def test_learning_snapshot_is_explicit_and_separate_from_the_refresh_poll(tmp_path) -> None:
    db, _, _ = _seed(tmp_path)
    data = ObservatoryData(_ctx(db))

    learning = data.learning_snapshot()

    assert learning.snapshots == []
    assert learning.facts == []
