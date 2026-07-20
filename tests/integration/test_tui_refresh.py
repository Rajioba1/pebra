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


def _snapshot_with_rows(*assessment_ids: str) -> ObservatorySnapshot:
    return ObservatorySnapshot(
        overview={"total": len(assessment_ids)},
        assessments=[
            {
                "assessment_id": assessment_id,
                "decision": "proceed",
                "assessed_commit": assessment_id,
                "terminal_status": None,
                "scores": {"rau": index / 100},
            }
            for index, assessment_id in enumerate(assessment_ids)
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


class _SensitiveData(_FakeData):
    repo_id = "repo-token-sk_live_DO_NOT_LOG"


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


def test_manual_refresh_reports_started_or_skipped(monkeypatch) -> None:
    screen = ObservatoryScreen(_FakeData())
    monkeypatch.setattr(screen, "_refresh_worker", lambda: None)

    assert screen.action_refresh() is True
    assert screen.action_refresh() is False


def test_finish_ignores_late_result_on_unmounted_screen() -> None:
    screen = ObservatoryScreen(_FakeData())
    screen._refreshing = True
    screen._finish_ok(_snapshot())  # screen was never mounted -> must not crash
    assert screen._refreshing is False
    screen._refreshing = True
    screen._finish_error("boom")
    assert screen._refreshing is False


@pytest.mark.parametrize("finish", ["ok", "error"])
def test_finish_ignores_result_while_screen_is_being_pruned(monkeypatch, finish: str) -> None:
    """Textual keeps is_mounted true briefly after pruning starts and children disappear."""
    screen = ObservatoryScreen(_FakeData())
    screen._refreshing = True
    screen._pruning = True
    monkeypatch.setattr(ObservatoryScreen, "is_mounted", property(lambda _self: True))

    def fail_if_called(*_args, **_kwargs) -> None:
        pytest.fail("a pruning screen must not update child widgets")

    monkeypatch.setattr(screen, "_apply_snapshot", fail_if_called)
    monkeypatch.setattr(screen, "_set_message", fail_if_called)

    if finish == "ok":
        screen._finish_ok(_snapshot())
    else:
        screen._finish_error("boom")

    assert screen._refreshing is False


def _capture_dev_logs(monkeypatch, *, mounted: bool = True) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(ObservatoryScreen, "is_mounted", property(lambda _self: mounted))
    monkeypatch.setattr(ObservatoryScreen, "log", property(lambda _self: messages.append))
    return messages


def test_dev_log_success_omits_sensitive_refresh_payload(monkeypatch) -> None:
    screen = ObservatoryScreen(_SensitiveData())
    snapshot = _snapshot()
    snapshot.assessments[0]["source"] = "source-password-DO_NOT_LOG"
    screen._refresh_started_at = 1.0
    monkeypatch.setattr("pebra.tui.screens.observatory.time.monotonic", lambda: 2.5)
    monkeypatch.setattr(screen, "_apply_snapshot", lambda _snapshot: None)
    messages = _capture_dev_logs(monkeypatch)

    screen._finish_ok(snapshot)

    assert messages == ["observatory refresh ok rows=1 duration=1.500s"]


def test_dev_log_error_omits_sensitive_error_and_token(monkeypatch) -> None:
    screen = ObservatoryScreen(_SensitiveData())
    monkeypatch.setattr(screen, "_set_message", lambda _message: None)
    messages = _capture_dev_logs(monkeypatch)

    screen._finish_error("Authorization: Bearer error-token-DO_NOT_LOG")

    assert messages == ["observatory refresh failed category=store_unavailable"]


def test_dev_log_busy_contains_only_safe_status(monkeypatch) -> None:
    screen = ObservatoryScreen(_SensitiveData())
    screen._refreshing = True
    messages = _capture_dev_logs(monkeypatch)

    assert screen.action_refresh() is False
    assert messages == ["observatory refresh skipped: busy"]


def test_dev_log_is_noop_when_unmounted(monkeypatch) -> None:
    screen = ObservatoryScreen(_SensitiveData())
    messages = _capture_dev_logs(monkeypatch, mounted=False)

    screen._dev_log("source-and-token-DO_NOT_LOG")

    assert messages == []


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


def test_successful_refresh_preserves_ledger_interaction_state() -> None:
    async def scenario() -> None:
        original_ids = tuple(f"asm_{index}" for index in range(30))

        class _ManyRowsData(_FakeData):
            def refresh_snapshot(self) -> ObservatorySnapshot:
                return _snapshot_with_rows(*original_ids)

        app = _Harness(ObservatoryScreen(_ManyRowsData()))
        async with app.run_test(size=(120, 18)) as pilot:
            screen = app.screen
            table = app.query_one("#ledger", DataTable)
            for _ in range(3):
                await pilot.pause()
            table.focus()
            table.move_cursor(row=20, column=5, scroll=False)
            await pilot.pause()
            table.scroll_to(x=12, y=12, animate=False, force=True, immediate=True)
            await pilot.pause()

            selected_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            old_scroll_y = table.scroll_y
            old_scroll_x = table.scroll_x
            assert selected_key == "asm_20"
            assert old_scroll_y > 0
            assert old_scroll_x > 0
            assert table.has_focus

            screen._apply_snapshot(_snapshot_with_rows("asm_new", *original_ids))
            await pilot.pause()

            refreshed_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            assert refreshed_key == selected_key
            assert table.cursor_coordinate.column == 5
            assert table.scroll_x == old_scroll_x
            assert table.scroll_y == old_scroll_y
            assert table.has_focus

    asyncio.run(scenario())


def test_refresh_preserves_scroll_when_selected_row_is_offscreen() -> None:
    async def scenario() -> None:
        assessment_ids = tuple(f"asm_{index}" for index in range(30))

        class _ManyRowsData(_FakeData):
            def refresh_snapshot(self) -> ObservatorySnapshot:
                return _snapshot_with_rows(*assessment_ids)

        app = _Harness(ObservatoryScreen(_ManyRowsData()))
        async with app.run_test(size=(120, 18)) as pilot:
            screen = app.screen
            table = app.query_one("#ledger", DataTable)
            for _ in range(3):
                await pilot.pause()
            table.focus()
            table.move_cursor(row=0, column=5, scroll=False)
            await pilot.pause()
            table.scroll_to(x=12, y=12, animate=False, force=True, immediate=True)
            await pilot.pause()

            old_scroll_x = table.scroll_x
            old_scroll_y = table.scroll_y
            assert old_scroll_x > 0
            assert old_scroll_y > 0

            screen._apply_snapshot(_snapshot_with_rows("asm_new", *assessment_ids))
            for _ in range(3):
                await pilot.pause()

            selected_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            assert selected_key == "asm_0"
            assert table.scroll_x == old_scroll_x
            assert table.scroll_y == old_scroll_y
            assert table.has_focus

    asyncio.run(scenario())


def test_late_scroll_restore_ignores_unmounted_screen(monkeypatch) -> None:
    screen = ObservatoryScreen(_FakeData())
    table = DataTable()
    monkeypatch.setattr(screen, "_can_update_children", lambda: False)

    def fail_if_called(*_args, **_kwargs) -> None:
        pytest.fail("late scroll callback must not touch an unmounted table")

    monkeypatch.setattr(table, "scroll_to", fail_if_called)

    screen._restore_ledger_scroll(table, x=12, y=12)


def test_grouped_refresh_preserves_exact_selection_focus_scroll_and_open_detail() -> None:
    def grouped_row(group: int, member: int) -> dict:
        return {
            "assessment_id": f"asm_{group}_{member}",
            "candidate_fingerprint": f"{group:064x}",
            "decision": "proceed",
            "assessed_commit": f"commit-{group}",
            "terminal_status": None,
            "task": f"Task {group}",
            "action_id": f"action-{group}",
            "target_files": [f"src/file_{group}.py"],
            "scores": {"rau": 0.2, "expected_loss": 0.1, "benefit": 0.3},
        }

    original_rows = [grouped_row(group, member) for group in range(15) for member in range(2)]

    class _GroupedData(_FakeData):
        def __init__(self) -> None:
            self.rows = original_rows

        def refresh_snapshot(self) -> ObservatorySnapshot:
            return ObservatorySnapshot(
                overview={"total": len(self.rows)},
                assessments=self.rows,
                scores_series=[{"rau": row["scores"]["rau"]} for row in self.rows],
                chain={"valid": True},
            )

    async def scenario() -> None:
        data = _GroupedData()
        screen = ObservatoryScreen(data)
        app = _Harness(screen)
        async with app.run_test(size=(120, 18)) as pilot:
            table = app.query_one("#ledger", DataTable)
            table.focus()
            table.move_cursor(row=21, column=5, scroll=False)
            await pilot.press("g")
            await pilot.pause()
            assert screen.group_repeats is True
            assert screen.selected_underlying_assessment_id == "asm_10_1"

            table.scroll_to(x=12, y=8, animate=False, force=True, immediate=True)
            await pilot.pause()
            old_scroll_x = table.scroll_x
            old_scroll_y = table.scroll_y
            assert old_scroll_x > 0 and old_scroll_y > 0 and table.has_focus

            await pilot.press("enter")
            await pilot.pause()
            detail_screen = app.screen
            data.rows = [grouped_row(0, 2), *original_rows]
            screen._refreshing = True
            screen._finish_ok(data.refresh_snapshot())
            await pilot.pause()

            assert app.screen is detail_screen
            assert screen.group_repeats is True
            assert screen.selected_underlying_assessment_id == "asm_10_1"
            assert table.has_focus
            assert table.scroll_x == old_scroll_x
            assert table.scroll_y == old_scroll_y

            app.pop_screen()
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert screen.group_repeats is False
            assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == "asm_10_1"

    asyncio.run(scenario())
