"""Tests for the assessment detail drill-in (Observatory TUI M4)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from textual.app import App  # noqa: E402
from textual.coordinate import Coordinate  # noqa: E402
from textual.screen import Screen  # noqa: E402
from textual.widgets import DataTable, Pretty  # noqa: E402

from pebra.app.observatory_query_controller import AssessmentNotFoundError  # noqa: E402
from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402
from pebra.tui.data import ObservatorySnapshot  # noqa: E402
from pebra.tui.screens.detail import (  # noqa: E402
    GATES_UNAVAILABLE_NOTE,
    AssessmentDetailScreen,
    detail_sections,
    header_line,
)
from pebra.tui.screens.observatory import ObservatoryScreen  # noqa: E402


def _detail() -> dict:
    return {
        "assessment_id": "asm_1",
        "content": {
            "repo_id": "r",
            "decision": "ask_human",
            "assessed_commit": "abc1234def",
            "scores": {
                "rau": -0.14,
                "benefit": 0.5,
                "symbol_scope_evidence": {"symbol_fanin": {"resolved_qualified_names": ["A::b"]}},
                "variance_breakdown": {"p_success": 0.4},
            },
        },
        "model_guidance_packet": {"decision": "ask_human"},
        "guardrails": [{"decision": "proceed"}],
        "outcomes": [],
    }


def test_sections_split_scores_from_evidence_and_carry_guidance() -> None:
    sections = dict(detail_sections(_detail()))
    assert "rau" in sections["Scores"] and "symbol_scope_evidence" not in sections["Scores"]
    assert set(sections["Evidence"]) == {"symbol_scope_evidence", "variance_breakdown"}
    assert sections["Guidance"] == {"decision": "ask_human"}
    assert sections["Guardrails"] == [{"decision": "proceed"}]


def test_header_line_summarizes_identity() -> None:
    line = header_line(_detail())
    assert "asm_1" in line and "ask_human" in line and "abc1234" in line and "repo r" in line


def test_gates_note_says_unavailable_and_is_not_reconstructed() -> None:
    assert "not available in history" in GATES_UNAVAILABLE_NOTE.lower()
    # The detail carries no gates_fired; the note is a constant, never derived from scores.
    assert "gate" in GATES_UNAVAILABLE_NOTE.lower()


# --- integration ---


def _seed(tmp_path) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.ASK_HUMAN,
            requires_confirmation=True,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"rau": -0.14, "benefit": 0.5, "expected_loss": 0.15},
            repo_id="r",
            repo_root="/x",
            model_guidance_packet={"decision": "ask_human"},
            assessed_commit="abc1234",
        ),
        {"task": "t"},
    )
    store.close()
    return db


def _ctx(db: str) -> ObservatoryContext:
    return ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)


def test_row_select_opens_detail_then_escape_returns(tmp_path) -> None:
    db = _seed(tmp_path)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx(db))
        async with app.run_test() as pilot:
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AssessmentDetailScreen)
            # persisted sections + the honest gates note are present
            assert app.screen.query("Pretty").results(Pretty)
            assert any(
                "not available in history" in str(s.render()).lower()
                for s in app.screen.query("#gates-note")
            )
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ObservatoryScreen)

    asyncio.run(scenario())


class _FakeData:
    repo_id = "r"

    def __init__(self, *, detail_error: Exception | None = None) -> None:
        self._detail_error = detail_error

    def refresh_snapshot(self) -> ObservatorySnapshot:
        return ObservatorySnapshot(
            overview={"total": 1},
            assessments=[
                {
                    "assessment_id": "asm_1",
                    "decision": "proceed",
                    "assessed_commit": "x",
                    "terminal_status": None,
                    "scores": {"rau": 0.1},
                }
            ],
            scores_series=[],
            chain={"valid": True},
        )

    def detail(self, assessment_id: str) -> dict:
        if self._detail_error is not None:
            raise self._detail_error
        return _detail()


class _Harness(App):
    def __init__(self, screen: Screen) -> None:
        super().__init__()
        self._screen = screen

    def get_default_screen(self) -> Screen:
        return self._screen


class _RecordingData(_FakeData):
    def __init__(self) -> None:
        super().__init__()
        self.detail_ids: list[str] = []

    def detail(self, assessment_id: str) -> dict:
        self.detail_ids.append(assessment_id)
        return _detail()


def test_not_found_detail_does_not_push_or_leak(tmp_path) -> None:
    async def scenario() -> None:
        screen = ObservatoryScreen(_FakeData(detail_error=AssessmentNotFoundError("asm_1")))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            app.query_one("#ledger", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            # guard held: still on the ledger, no detail screen pushed
            assert isinstance(app.screen, ObservatoryScreen)

    asyncio.run(scenario())


def test_row_selection_event_keeps_assessment_identity_across_refresh() -> None:
    async def scenario() -> None:
        data = _RecordingData()
        screen = ObservatoryScreen(data)
        app = _Harness(screen)
        async with app.run_test():
            table = app.query_one("#ledger", DataTable)
            old_row_key = table.coordinate_to_cell_key(Coordinate(0, 0)).row_key
            old_event = DataTable.RowSelected(table, 0, old_row_key)

            refreshed = data.refresh_snapshot()
            refreshed.assessments[0]["assessment_id"] = "asm_2"
            screen._apply_snapshot(refreshed)
            app.push_screen = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            screen.on_data_table_row_selected(old_event)

            assert data.detail_ids == ["asm_1"]

    asyncio.run(scenario())
