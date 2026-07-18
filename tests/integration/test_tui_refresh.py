"""Tests for the single-flight live refresh (Observatory TUI M4).

Guarantees: one refresh at a time (skip, never cancel), the 5s timer skips while busy, manual refresh
works when idle, a failed refresh preserves the last good render, results are applied on the UI thread,
and a late result never touches a screen that has gone away.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from textual.app import App  # noqa: E402
from textual.screen import Screen  # noqa: E402
from textual.widgets import DataTable  # noqa: E402

from pebra.tui.data import ObservatorySnapshot, ObservatoryStoreUnavailable  # noqa: E402
from pebra.tui.screens.observatory import ObservatoryScreen  # noqa: E402


def _snapshot() -> ObservatorySnapshot:
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


class _FakeData:
    repo_id = "r"

    def refresh_snapshot(self) -> ObservatorySnapshot:
        return _snapshot()

    def detail(self, assessment_id: str) -> dict:
        return {}


class _BlockingData:
    repo_id = "r"

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()  # the synchronous mount-time load passes straight through
        self.calls = 0
        self.fail = False

    def refresh_snapshot(self) -> ObservatorySnapshot:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=5)
        if self.fail:
            raise ObservatoryStoreUnavailable("boom")
        return _snapshot()

    def detail(self, assessment_id: str) -> dict:
        return {}


class _Harness(App):
    def __init__(self, screen: Screen) -> None:
        super().__init__()
        self._screen = screen

    def get_default_screen(self) -> Screen:
        return self._screen


# --- unit: guard logic, no threads ---


def test_single_flight_guard_skips_while_busy() -> None:
    screen = ObservatoryScreen(_FakeData())
    assert screen._try_begin_refresh() is True
    assert screen._refreshing is True
    assert screen._try_begin_refresh() is False  # busy -> skip, do not start a second
    screen._refreshing = False
    assert screen._try_begin_refresh() is True


def test_finish_ignores_late_result_on_unmounted_screen() -> None:
    screen = ObservatoryScreen(_FakeData())
    screen._refreshing = True
    screen._finish_ok(_snapshot())  # screen was never mounted -> must not crash
    assert screen._refreshing is False
    screen._refreshing = True
    screen._finish_error("boom")
    assert screen._refreshing is False


# --- integration: threaded worker ---


async def _wait_until(pilot, predicate, *, tries: int = 40) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


def test_overlapping_triggers_run_only_one_refresh(tmp_path) -> None:
    async def scenario() -> None:
        data = _BlockingData()
        app = _Harness(ObservatoryScreen(data))
        async with app.run_test() as pilot:
            screen = app.screen
            await _wait_until(pilot, lambda: data.calls == 1)  # mount load
            data.release.clear()
            data.entered.clear()

            screen.action_refresh()  # starts the (blocking) worker
            # Wait via pilot.pause so the event loop can actually launch the worker thread (a blocking
            # Event.wait here would freeze the loop before the thread starts).
            assert await _wait_until(pilot, data.entered.is_set)
            assert screen._refreshing is True

            screen.action_refresh()  # skipped while busy
            screen._tick()           # skipped while busy
            assert data.calls == 2   # only the one in-flight refresh ran

            data.release.set()
            assert await _wait_until(pilot, lambda: not screen._refreshing)
            assert data.calls == 2

    asyncio.run(scenario())


def test_failed_refresh_preserves_previous_rows(tmp_path) -> None:
    async def scenario() -> None:
        data = _BlockingData()
        app = _Harness(ObservatoryScreen(data))
        async with app.run_test() as pilot:
            screen = app.screen
            table = app.query_one("#ledger", DataTable)
            await _wait_until(pilot, lambda: table.row_count == 1)

            data.fail = True
            screen.action_refresh()
            assert await _wait_until(pilot, lambda: not screen._refreshing and bool(screen.message_text))

            assert table.row_count == 1  # last good render preserved
            assert "unavailable" in screen.message_text.lower()

    asyncio.run(scenario())


def test_manual_refresh_runs_when_idle(tmp_path) -> None:
    async def scenario() -> None:
        data = _BlockingData()
        app = _Harness(ObservatoryScreen(data))
        async with app.run_test() as pilot:
            screen = app.screen
            await _wait_until(pilot, lambda: data.calls == 1)
            screen.action_refresh()
            assert await _wait_until(pilot, lambda: data.calls == 2 and not screen._refreshing)

    asyncio.run(scenario())