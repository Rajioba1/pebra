"""Integration tests for the ObservatoryData read facade (Observatory TUI M3).

ObservatoryData opens a short-lived read-only SqliteStore per read session, delegates to the M1 shared
query controller, and closes in finally. It shapes nothing and re-derives no decisions — it only forwards
controller output. These tests drive it against a real seeded store.
"""

from __future__ import annotations

import pytest

from pebra.adapters.store.db import SqliteStore
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
