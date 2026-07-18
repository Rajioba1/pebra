"""ObservatoryScreen — the main read-only ledger view (Observatory TUI M3).

Composes the status header, the assessment ledger (DataTable with the RAU-lane + colored decision), and
a message line used for the empty state and a durable load error. It loads one snapshot on mount; the
live single-flight refresh loop arrives in M4. It reads only through ObservatoryData (no store/decision
logic here).
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Header, Static

from pebra.tui.data import ObservatoryData, ObservatorySnapshot, ObservatoryStoreUnavailable
from pebra.tui.widgets.ledger_table import (
    LEDGER_COLUMNS,
    LEDGER_COLUMN_WIDTHS,
    decision_cell,
    ledger_row,
)
from pebra.tui.widgets.status_header import StatusHeader

_EMPTY = "No assessments recorded for this repository yet."
_DECISION_COLUMN_INDEX = LEDGER_COLUMNS.index("decision")


class ObservatoryScreen(Screen):
    def __init__(self, data: ObservatoryData) -> None:
        super().__init__()
        self._data = data
        self._rows: list[dict[str, Any]] = []
        self.message_text = ""  # current empty-state / error text ("" when the ledger has rows)

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusHeader(id="status")
        yield DataTable(id="ledger", cursor_type="row", zebra_stripes=True)
        yield Static("", id="ledger-message")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#ledger", DataTable)
        for label in LEDGER_COLUMNS:
            table.add_column(label, width=LEDGER_COLUMN_WIDTHS.get(label))
        self.app.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self.reload()

    def reload(self) -> None:
        try:
            snapshot = self._data.refresh_snapshot()
        except ObservatoryStoreUnavailable as exc:
            # Durable error — keep whatever is already on screen; do not crash the surface.
            self._set_message(f"Assessment store unavailable — {exc}")
            return
        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: ObservatorySnapshot) -> None:  # not `_render`: Textual internal
        rows = snapshot.assessments
        self._rows = rows
        latest_commit = rows[0].get("assessed_commit") if rows else None
        self.query_one("#status", StatusHeader).update_status(
            repo_id=self._data.repo_id,
            latest_commit=latest_commit,
            chain_valid=bool(snapshot.chain.get("valid")),
            total=int(snapshot.overview.get("total", 0)),
        )
        table = self.query_one("#ledger", DataTable)
        table.clear()
        dark = self._is_dark()
        for row in rows:
            table.add_row(*ledger_row(row, dark=dark))
        self._set_message("" if rows else _EMPTY)

    def _on_theme_changed(self, theme: Theme) -> None:
        table = self.query_one("#ledger", DataTable)
        for row_index, row in enumerate(self._rows):
            table.update_cell_at(
                Coordinate(row_index, _DECISION_COLUMN_INDEX),
                decision_cell(str(row.get("decision", "")), dark=theme.dark),
            )

    def _is_dark(self) -> bool:
        theme = getattr(self.app, "current_theme", None)
        return bool(getattr(theme, "dark", True))

    def _set_message(self, text: str) -> None:
        self.message_text = text
        message = self.query_one("#ledger-message", Static)
        message.update(text)
        message.display = bool(text)
