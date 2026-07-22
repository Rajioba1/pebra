"""Read-only Learning-screen behavior (Observatory M4)."""

from __future__ import annotations

import asyncio
from threading import Event

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from textual.app import App
from textual.screen import Screen
from textual.widgets import DataTable, Static

from pebra.tui.data import ObservatoryLearningSnapshot, ObservatoryStoreUnavailable
from pebra.tui.screens.learning import LearningScreen


class _Data:
    repo_id = "r"

    def __init__(self, snapshot: ObservatoryLearningSnapshot | Exception) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def learning_snapshot(self) -> ObservatoryLearningSnapshot:
        self.calls += 1
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


class _Harness(App):
    def __init__(self, screen: Screen) -> None:
        super().__init__()
        self._screen = screen

    def get_default_screen(self) -> Screen:
        return self._screen


async def _pause_until(predicate, pilot, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_learning_screen_has_honest_empty_states_and_escape_returns() -> None:
    async def scenario() -> None:
        data = _Data(ObservatoryLearningSnapshot([], []))
        screen = LearningScreen(data)  # type: ignore[arg-type]
        app = _Harness(Screen())
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await _pause_until(lambda: not screen.loading, pilot)
            assert "No promoted local snapshot" in screen.query_one(
                "#learning-snapshots-empty", Static
            ).render().plain
            assert "Recorded outcomes do not become active" in screen.query_one(
                "#learning-facts-empty", Static
            ).render().plain
            assert "No verified completed outcomes" in screen.query_one(
                "#learning-context-empty", Static
            ).render().plain
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not screen

    asyncio.run(scenario())


def test_learning_screen_renders_active_rows_as_literal_content_and_refreshes_only_on_demand() -> None:
    async def scenario() -> None:
        data = _Data(
            ObservatoryLearningSnapshot(
                [
                    {
                        "snapshot_id": "rs_[7]",
                        "status": "active",
                        "created_at": "2026-07-22T12:00:00+00:00",
                        "activated_at": "2026-07-22T12:01:00+00:00",
                        "promotion_reason": "[bold] verified",
                    }
                ],
                [
                    {
                        "fact_id": "lrf_1",
                        "snapshot_id": "rs_[7]",
                        "target_name": "Dict[str, Any]",
                        "status": "active",
                        "created_at": "2026-07-22T12:00:00+00:00",
                    }
                ],
                {
                    "status": "available",
                    "items": [{
                        "learning_context_id": "lc_[1]",
                        "assessment_id": "asm_1",
                        "task": "Fix Dict[str, Any]",
                        "lesson": "Verified [bold] lesson",
                        "terminal_status": "completed",
                        "verification_summary": "PEBRA verify proceeded",
                        "created_at": "2026-07-22T12:00:00+00:00",
                    }],
                },
            )
        )
        screen = LearningScreen(data)  # type: ignore[arg-type]
        async with _Harness(screen).run_test() as pilot:
            await _pause_until(lambda: not screen.loading, pilot)
            snapshots = screen.query_one("#learning-snapshots", DataTable)
            facts = screen.query_one("#learning-facts", DataTable)
            assert snapshots.row_count == facts.row_count == 1
            assert snapshots.get_cell_at((0, 0)).plain == "rs_[7]"
            assert snapshots.get_cell_at((0, 4)).plain == "[bold] verified"
            assert facts.get_cell_at((0, 2)).plain == "Dict[str, Any]"
            lessons = screen.query_one("#learning-context", DataTable)
            assert [column.label.plain for column in lessons.columns.values()] == [
                "record", "assessment", "task", "lesson", "verified outcome", "created"
            ]
            assert lessons.get_cell_at((0, 0)).plain == "lc_[1]"
            assert lessons.get_cell_at((0, 2)).plain == "Fix Dict[str, Any]"
            assert lessons.get_cell_at((0, 3)).plain == "Verified [bold] lesson"
            assert lessons.get_cell_at((0, 4)).plain == "PEBRA verify proceeded"
            assert data.calls == 1
            await pilot.press("r")
            await _pause_until(lambda: not screen.loading, pilot)
            assert data.calls == 2

    asyncio.run(scenario())


def test_learning_refresh_failure_preserves_last_good_tables() -> None:
    async def scenario() -> None:
        data = _Data(
            ObservatoryLearningSnapshot(
                [{"snapshot_id": "rs_1", "status": "active", "created_at": "now"}], []
            )
        )
        screen = LearningScreen(data)  # type: ignore[arg-type]
        async with _Harness(screen).run_test() as pilot:
            await _pause_until(lambda: not screen.loading, pilot)
            assert screen.query_one("#learning-snapshots", DataTable).row_count == 1
            data.snapshot = ObservatoryStoreUnavailable("read failure")
            await pilot.press("r")
            await _pause_until(lambda: not screen.loading, pilot)
            assert screen.query_one("#learning-snapshots", DataTable).row_count == 1
            assert "unavailable" in screen.query_one("#learning-message", Static).render().plain

    asyncio.run(scenario())


def test_learning_resize_does_not_drop_columns() -> None:
    async def scenario() -> None:
        data = _Data(ObservatoryLearningSnapshot([], []))
        screen = LearningScreen(data)  # type: ignore[arg-type]
        async with _Harness(screen).run_test(size=(100, 30)) as pilot:
            await _pause_until(lambda: not screen.loading, pilot)
            facts = screen.query_one("#learning-facts", DataTable)
            expected = list(facts.columns)
            await pilot.resize_terminal(70, 24)
            await pilot.pause()
            assert list(facts.columns) == expected

    asyncio.run(scenario())


def test_learning_screen_distinguishes_shadow_snapshot_and_candidate_fact() -> None:
    async def scenario() -> None:
        data = _Data(
            ObservatoryLearningSnapshot(
                [{"snapshot_id": "rs_2", "status": "shadow", "created_at": "2026-07-22"}],
                [
                    {
                        "fact_id": "lrf_2",
                        "snapshot_id": "rs_2",
                        "target_name": "p_event.auth",
                        "status": "candidate",
                        "created_at": "2026-07-22",
                    }
                ],
            )
        )
        screen = LearningScreen(data)  # type: ignore[arg-type]
        async with _Harness(screen).run_test() as pilot:
            await _pause_until(lambda: not screen.loading, pilot)
            snapshots = screen.query_one("#learning-snapshots", DataTable)
            facts = screen.query_one("#learning-facts", DataTable)
            assert snapshots.get_cell_at((0, 1)).plain == "shadow"
            assert facts.get_cell_at((0, 3)).plain == "candidate"

    asyncio.run(scenario())


def test_malformed_learning_rows_degrade_to_unavailable_cells() -> None:
    async def scenario() -> None:
        data = _Data(
            ObservatoryLearningSnapshot(
                [None],  # type: ignore[list-item]
                [{"fact_id": ["bad"], "status": True}],
            )
        )
        screen = LearningScreen(data)  # type: ignore[arg-type]
        async with _Harness(screen).run_test() as pilot:
            await _pause_until(lambda: not screen.loading, pilot)
            snapshots = screen.query_one("#learning-snapshots", DataTable)
            facts = screen.query_one("#learning-facts", DataTable)
            assert snapshots.get_cell_at((0, 0)).plain == "—"
            assert snapshots.get_cell_at((0, 1)).plain == "unavailable"
            assert facts.get_cell_at((0, 0)).plain == "—"
            assert facts.get_cell_at((0, 3)).plain == "unavailable"

    asyncio.run(scenario())


def test_inflight_learning_delivery_cannot_touch_dismissed_screen() -> None:
    started = Event()
    release = Event()
    completed = Event()
    finish_callback_delivered = Event()

    class _BlockingData(_Data):
        def learning_snapshot(self) -> ObservatoryLearningSnapshot:
            self.calls += 1
            started.set()
            release.wait(timeout=5)
            completed.set()
            return ObservatoryLearningSnapshot(
                [{"snapshot_id": "rs_1", "status": "active"}], []
            )

    async def scenario() -> None:
        data = _BlockingData(ObservatoryLearningSnapshot([], []))
        screen = LearningScreen(data)  # type: ignore[arg-type]
        app = _Harness(Screen())
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await _pause_until(started.is_set, pilot)
            stale_generation = screen._generation
            await app.pop_screen()
            await _pause_until(lambda: not screen._can_update_children(), pilot)

            def deliver_queued_finish() -> None:
                screen._finish_ok(
                    stale_generation,
                    ObservatoryLearningSnapshot(
                        [{"snapshot_id": "rs_late", "status": "active"}], []
                    ),
                )
                finish_callback_delivered.set()

            app.call_later(deliver_queued_finish)
            await _pause_until(finish_callback_delivered.is_set, pilot)
            release.set()
            await _pause_until(completed.is_set, pilot)
            assert not screen.loading
            assert len(screen.query("#learning-snapshots")) == 0

    asyncio.run(scenario())
