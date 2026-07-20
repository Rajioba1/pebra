"""ObservatoryScreen — the main read-only ledger view (Observatory TUI M3).

Composes the status header, the assessment ledger (DataTable with the RAU-lane + colored decision), and
a message line used for the empty state and a durable load error. It loads one snapshot on mount; the
live single-flight refresh loop arrives in M4. It reads only through ObservatoryData (no store/decision
logic here).
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from textual import events, work
from textual.app import ComposeResult
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Header, Static
from textual.widgets.data_table import RowDoesNotExist

from pebra.app.observatory_query_controller import AssessmentNotFoundError
from pebra.tui.exploration import RepositoryExplorationCoordinator
from pebra.tui.data import ObservatoryData, ObservatorySnapshot, ObservatoryStoreUnavailable
from pebra.tui.ledger_groups import LedgerGroup, group_contiguous_assessments
from pebra.tui.screens.detail import AssessmentDetailScreen
from pebra.tui.widgets.banner import PebraBanner
from pebra.tui.widgets.ledger_table import (
    LEDGER_COLUMN_WIDTHS,
    LEDGER_LABELS,
    columns_for_width,
    decision_cell,
    ledger_row,
)
from pebra.tui.widgets.score_sparklines import ScoreSparklines
from pebra.tui.widgets.status_header import StatusHeader

_EMPTY = "No assessments recorded for this repository yet."
_SCROLL_HINT = "←/→ more columns · Home/End"
_REFRESH_INTERVAL = 5.0  # seconds; SQLite-only poll — never the graph/RCA engines


class ObservatoryScreen(Screen):
    BINDINGS = [("r", "refresh", "Refresh"), ("g", "toggle_grouping", "Group repeats")]

    def __init__(
        self,
        data: ObservatoryData,
        *,
        repo_root: str | None = None,
        exploration: RepositoryExplorationCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._data = data
        self._repo_root = repo_root
        self._exploration = exploration
        self._rows: list[dict[str, Any]] = []
        self._overview: dict[str, Any] = {}
        self._ledger_columns: tuple[str, ...] = ()
        self.group_repeats = False
        self._selected_underlying_id: str | None = None
        self.message_text = ""  # current empty-state / error text ("" when the ledger has rows)
        self._refreshing = False  # single-flight guard, only read/written on the UI thread
        self._refresh_started_at = 0.0  # monotonic clock, for dev-console refresh-duration logging

    def overview_summary(self) -> str:
        total = int(self._overview.get("total", 0))
        by_decision = self._overview.get("by_decision") or {}
        parts = "   ".join(f"{name} {count}" for name, count in by_decision.items()) or "none"
        return f"{total} assessments — {parts}"

    def compose(self) -> ComposeResult:
        yield Header()
        yield PebraBanner(id="banner")
        yield StatusHeader(id="status")
        # fixed_columns=1 pins the asm-id column so it stays visible when the table is scrolled right.
        yield DataTable(id="ledger", cursor_type="row", zebra_stripes=True, fixed_columns=1)
        yield Static("", id="ledger-caption")
        yield Static("", id="scroll-hint")
        yield ScoreSparklines(id="trends")
        yield Static("", id="ledger-message")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_ledger_columns(columns_for_width(self.size.width))
        self.app.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self.reload()  # initial load is synchronous so the first paint has data
        self.set_interval(_REFRESH_INTERVAL, self._tick)

    def reload(self) -> None:
        try:
            snapshot = self._data.refresh_snapshot()
        except ObservatoryStoreUnavailable as exc:
            # Durable error — keep whatever is already on screen; do not crash the surface.
            self._set_message(f"Assessment store unavailable — {exc}")
            return
        self._apply_snapshot(snapshot)

    def on_resize(self, event: events.Resize) -> None:
        columns = columns_for_width(event.size.width)
        if self._ledger_columns and columns != self._ledger_columns:
            self._rebuild_ledger_columns(columns)
        # The overflow hint depends on the settled layout, so update it after the next refresh.
        self.call_after_refresh(self._update_scroll_hint)

    def _selected_assessment_id(self, table: DataTable) -> str | None:
        row = table.cursor_coordinate.row
        if not table.row_count or row >= table.row_count:
            return None
        return table.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value

    @property
    def selected_underlying_assessment_id(self) -> str | None:
        return self._selected_underlying_id

    def _visible_groups(self) -> tuple[LedgerGroup, ...]:
        if self.group_repeats:
            return group_contiguous_assessments(self._rows)
        return tuple(
            LedgerGroup(
                primary_assessment_id=str(row["assessment_id"]),
                assessment_ids=(str(row["assessment_id"]),),
                latest_row=row,
            )
            for row in self._rows
        )

    def _capture_selected_underlying(self, table: DataTable) -> str | None:
        displayed_id = self._selected_assessment_id(table)
        if displayed_id is None:
            return self._selected_underlying_id
        if not self.group_repeats:
            self._selected_underlying_id = displayed_id
            return displayed_id
        for group in self._visible_groups():
            if group.primary_assessment_id == displayed_id:
                if self._selected_underlying_id not in group.assessment_ids:
                    self._selected_underlying_id = group.primary_assessment_id
                return self._selected_underlying_id
        self._selected_underlying_id = displayed_id
        return displayed_id

    def _displayed_id_for(self, assessment_id: str | None) -> str | None:
        if assessment_id is None or not self.group_repeats:
            return assessment_id
        for group in self._visible_groups():
            if assessment_id in group.assessment_ids:
                return group.primary_assessment_id
        return assessment_id

    def _add_ledger_rows(self, table: DataTable) -> None:
        dark = self._is_dark()
        for group in self._visible_groups():
            table.add_row(
                *ledger_row(
                    group.latest_row,
                    columns=self._ledger_columns,
                    dark=dark,
                    group_size=len(group.assessment_ids),
                ),
                key=group.primary_assessment_id,
            )

    def _restore_cursor(
        self,
        table: DataTable,
        *,
        assessment_id: str | None,
        fallback_row: int,
        column: int,
    ) -> None:
        if not table.row_count:
            return
        try:
            displayed_id = self._displayed_id_for(assessment_id)
            restored_row = table.get_row_index(displayed_id) if displayed_id else fallback_row
        except RowDoesNotExist:
            restored_row = fallback_row
        table.move_cursor(
            row=min(restored_row, table.row_count - 1),
            column=min(column, len(self._ledger_columns) - 1),
            scroll=False,
        )

    def _restore_ledger_scroll(self, table: DataTable, *, x: float, y: float) -> None:
        """Restore after Textual's deferred cursor-visibility work, unless the screen is going away."""
        if not self._can_update_children() or not table.is_mounted:
            return
        table.scroll_to(x=x, y=y, animate=False, force=True, immediate=True)

    def _rebuild_ledger_columns(self, columns: tuple[str, ...]) -> None:
        """Rebuild only across a width breakpoint; retain row identity/focus, reset horizontal view."""
        table = self.query_one("#ledger", DataTable)
        selected_id = self._capture_selected_underlying(table)
        old_row = table.cursor_coordinate.row
        old_column = table.cursor_coordinate.column
        old_scroll_y = table.scroll_y
        had_focus = table.has_focus
        table.clear(columns=True)
        self._ledger_columns = columns
        for column in columns:
            table.add_column(
                LEDGER_LABELS[column], key=column, width=LEDGER_COLUMN_WIDTHS.get(column)
            )
        self._add_ledger_rows(table)
        self._restore_cursor(
            table,
            assessment_id=selected_id,
            fallback_row=old_row,
            column=old_column,
        )
        if had_focus:
            table.focus(scroll_visible=False)
        table.scroll_to(x=0, y=old_scroll_y, animate=False, force=True, immediate=True)
        table.call_after_refresh(
            self._restore_ledger_scroll, table, x=0, y=old_scroll_y
        )

    def action_toggle_grouping(self) -> None:
        table = self.query_one("#ledger", DataTable)
        selected_id = self._capture_selected_underlying(table)
        old_row = table.cursor_coordinate.row
        old_column = table.cursor_coordinate.column
        old_scroll_x = table.scroll_x
        old_scroll_y = table.scroll_y
        had_focus = table.has_focus
        self.group_repeats = not self.group_repeats
        table.clear()
        self._add_ledger_rows(table)
        self._restore_cursor(
            table,
            assessment_id=selected_id,
            fallback_row=old_row,
            column=old_column,
        )
        if had_focus:
            table.focus(scroll_visible=False)
        table.scroll_to(
            x=old_scroll_x, y=old_scroll_y, animate=False, force=True, immediate=True
        )
        table.call_after_refresh(
            self._restore_ledger_scroll, table, x=old_scroll_x, y=old_scroll_y
        )
        self._update_ledger_caption()
        bindings = self._bindings.key_to_bindings.get("g", [])
        description = "Show raw" if self.group_repeats else "Group repeats"
        self._bindings.key_to_bindings["g"] = [
            replace(binding, description=description) for binding in bindings
        ]
        self.refresh_bindings()
        self.call_after_refresh(self._update_scroll_hint)

    def _update_ledger_caption(self) -> None:
        caption = self.query_one("#ledger-caption", Static)
        if self.group_repeats:
            caption.update(
                f"{len(self._visible_groups())} groups / {len(self._rows)} assessments"
            )
        caption.display = self.group_repeats

    def _update_scroll_hint(self) -> None:
        # Show the affordance only when the active breakpoint's columns actually overflow the pane.
        if not self._can_update_children():
            return
        overflowing = self.query_one("#ledger", DataTable).max_scroll_x > 0
        hint = self.query_one("#scroll-hint", Static)
        if overflowing:
            hint.update(_SCROLL_HINT)
        hint.display = overflowing

    # --- live single-flight refresh (5s poll + manual `r`) ---

    def _tick(self) -> None:
        self._start_refresh()

    def action_refresh(self) -> bool:
        return self._start_refresh()

    def _dev_log(self, message: str) -> None:
        # Safe dev-console logging (routes to `textual console`, never stdout). No-op when unmounted so
        # unit tests that call refresh callbacks directly don't need a running app.
        if self._can_update_children():
            self.log(message)

    def _can_update_children(self) -> bool:
        # Textual sets _pruning before detaching a screen's children, while is_mounted can remain true.
        # A worker callback queued in that interval must not query widgets that are already gone.
        return self.is_mounted and not self._pruning

    def _try_begin_refresh(self) -> bool:
        """Single-flight guard (UI thread only): claim the in-flight slot, or refuse if already busy.
        We SKIP overlapping refreshes — we never cancel the running worker to gate it."""
        if self._refreshing:
            return False
        self._refreshing = True
        return True

    def _start_refresh(self) -> bool:
        if not self._try_begin_refresh():
            self._dev_log("observatory refresh skipped: busy")
            return False
        self._refresh_started_at = time.monotonic()
        self._refresh_worker()
        return True

    @work(thread=True)
    def _refresh_worker(self) -> None:
        # Blocking SQLite read off the UI thread; results marshalled back via call_from_thread.
        try:
            snapshot = self._data.refresh_snapshot()
        except ObservatoryStoreUnavailable as exc:
            self.app.call_from_thread(self._finish_error, str(exc))
            return
        self.app.call_from_thread(self._finish_ok, snapshot)

    def _finish_ok(self, snapshot: ObservatorySnapshot) -> None:
        self._refreshing = False
        # Safe dev-console log: counts + timing only — the TUI never handles source/tokens/candidates.
        self._dev_log(
            f"observatory refresh ok rows={len(snapshot.assessments)} "
            f"duration={time.monotonic() - self._refresh_started_at:.3f}s"
        )
        if not self._can_update_children():  # a late result must never touch a screen that's going/gone
            return
        self._apply_snapshot(snapshot)

    def _finish_error(self, message: str) -> None:
        self._refreshing = False
        # Log the error CATEGORY, not the message contents.
        self._dev_log("observatory refresh failed category=store_unavailable")
        if not self._can_update_children():
            return
        # Preserve the last good render (do not clear the table); just surface the error.
        self._set_message(f"Assessment store unavailable — {message}")

    def _apply_snapshot(self, snapshot: ObservatorySnapshot) -> None:  # not `_render`: Textual internal
        rows = snapshot.assessments
        table = self.query_one("#ledger", DataTable)
        old_row = table.cursor_coordinate.row
        old_column = table.cursor_coordinate.column
        old_row_key = self._capture_selected_underlying(table)
        old_scroll_x = table.scroll_x
        old_scroll_y = table.scroll_y
        had_focus = table.has_focus
        self._rows = rows
        self._overview = snapshot.overview
        latest_commit = rows[0].get("assessed_commit") if rows else None
        self.query_one("#status", StatusHeader).update_status(
            repo_id=self._data.repo_id,
            latest_commit=latest_commit,
            chain_valid=bool(snapshot.chain.get("valid")),
            total=int(snapshot.overview.get("total", 0)),
        )
        table.clear()
        self._add_ledger_rows(table)
        self._restore_cursor(
            table,
            assessment_id=old_row_key,
            fallback_row=old_row,
            column=old_column,
        )
        if had_focus:
            table.focus(scroll_visible=False)
        table.scroll_to(
            x=old_scroll_x,
            y=old_scroll_y,
            animate=False,
            force=True,
            immediate=True,
        )
        table.call_after_refresh(
            self._restore_ledger_scroll,
            table,
            x=old_scroll_x,
            y=old_scroll_y,
        )
        self.query_one("#trends", ScoreSparklines).update_series(snapshot.scores_series)
        self._update_ledger_caption()
        self._set_message("" if rows else _EMPTY)
        # Auto-sized columns may become wider or narrower when new rows arrive, even though the
        # terminal itself did not resize. Recompute the overflow affordance after layout settles.
        self.call_after_refresh(self._update_scroll_hint)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        assessment_id = event.row_key.value
        if not assessment_id:
            return
        try:
            detail = self._data.detail(assessment_id)
        except AssessmentNotFoundError:
            # Missing or belongs to another repo — say so, never leak or reconstruct it.
            self.notify("Assessment not available.", severity="warning")
            return
        except ObservatoryStoreUnavailable as exc:
            self.notify(f"Assessment store unavailable — {exc}", severity="error")
            return
        assessment_ids = (assessment_id,)
        if self.group_repeats:
            for group in self._visible_groups():
                if group.primary_assessment_id == assessment_id:
                    assessment_ids = group.assessment_ids
                    break
        if self._selected_underlying_id not in assessment_ids:
            self._selected_underlying_id = assessment_id
        self.app.push_screen(
            AssessmentDetailScreen(
                detail,
                assessment_ids=assessment_ids,
                repo_root=self._repo_root,
                exploration=self._exploration,
            )
        )

    def _on_theme_changed(self, theme: Theme) -> None:
        table = self.query_one("#ledger", DataTable)
        decision_column = self._ledger_columns.index("decision")
        for row_index, group in enumerate(self._visible_groups()):
            table.update_cell_at(
                Coordinate(row_index, decision_column),
                decision_cell(str(group.latest_row.get("decision", "")), dark=theme.dark),
            )

    def _is_dark(self) -> bool:
        theme = getattr(self.app, "current_theme", None)
        return bool(getattr(theme, "dark", True))

    def _set_message(self, text: str) -> None:
        self.message_text = text
        message = self.query_one("#ledger-message", Static)
        message.update(text)
        message.display = bool(text)
