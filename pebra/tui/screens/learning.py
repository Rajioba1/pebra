"""Read-only Observatory learning lifecycle view.

Learning data is loaded deliberately, never by the five-second assessment-ledger poll.  The screen
uses the same single-flight, generation-checked, preserve-last-good delivery shape as repository
exploration, but its worker is only a short-lived SQLite read through ``ObservatoryData``.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from pebra.tui.data import (
    ObservatoryData,
    ObservatoryLearningSnapshot,
    ObservatoryStoreUnavailable,
)

_EMPTY_SNAPSHOTS = "No promoted local snapshot is active for this repository."
_EMPTY_FACTS = (
    "No learned facts are available. Recorded outcomes do not become active until learn and "
    "promotion gates succeed."
)
_EMPTY_CONTEXT = "No verified completed outcomes have produced recallable lessons yet."
_UNAVAILABLE_CONTEXT = "Verified lesson history is unavailable or failed integrity validation."


def _text(value: object, *, fallback: str = "—") -> str:
    return value if isinstance(value, str) and value else fallback


def _snapshot_row(snapshot: object) -> tuple[Content, Content, Content, Content, Content]:
    """Literal-safe compact snapshot lifecycle row from the shared DTO."""
    data = snapshot if isinstance(snapshot, dict) else {}
    return tuple(
        Content(value)
        for value in (
            _text(data.get("snapshot_id")),
            _text(data.get("status"), fallback="unavailable"),
            _text(data.get("created_at"))[:16].replace("T", " "),
            _text(data.get("activated_at")),
            _text(data.get("promotion_reason")),
        )
    )  # type: ignore[return-value]


def _fact_row(fact: object) -> tuple[Content, Content, Content, Content, Content]:
    """Literal-safe compact learned-fact row; fact JSON stays out of the overview table."""
    data = fact if isinstance(fact, dict) else {}
    return tuple(
        Content(value)
        for value in (
            _text(data.get("fact_id")),
            _text(data.get("snapshot_id")),
            _text(data.get("target_name")),
            _text(data.get("status"), fallback="unavailable"),
            _text(data.get("created_at"))[:16].replace("T", " "),
        )
    )  # type: ignore[return-value]


def _context_row(item: object) -> tuple[Content, Content, Content, Content, Content]:
    data = item if isinstance(item, dict) else {}
    return tuple(
        Content(value)
        for value in (
            _text(data.get("learning_context_id")),
            _text(data.get("assessment_id")),
            _text(data.get("task")),
            _text(data.get("lesson")),
            _text(data.get("created_at"))[:16].replace("T", " "),
        )
    )  # type: ignore[return-value]


class LearningScreen(Screen):
    """Explicit read-only snapshots/facts view with single-flight refresh semantics."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, data: ObservatoryData) -> None:
        super().__init__()
        self._data = data
        self._loading = False
        self._generation = 0
        self._snapshot: ObservatoryLearningSnapshot | None = None

    @property
    def loading(self) -> bool:
        return self._loading

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="learning-body"):
            yield Static("Learning lifecycle", id="learning-status", markup=False)
            yield Static("Snapshots", classes="section-title")
            yield DataTable(id="learning-snapshots", classes="learning-table", cursor_type="row")
            yield Static("", id="learning-snapshots-empty", markup=False)
            yield Static("Learned facts", classes="section-title")
            yield DataTable(id="learning-facts", classes="learning-table", cursor_type="row")
            yield Static("", id="learning-facts-empty", markup=False)
            yield Static("Verified lessons", classes="section-title")
            yield DataTable(id="learning-context", classes="learning-table", cursor_type="row")
            yield Static("", id="learning-context-empty", markup=False)
            yield Static("", id="learning-message", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        snapshots = self.query_one("#learning-snapshots", DataTable)
        snapshots.add_columns("snapshot", "status", "created", "activated", "promotion")
        facts = self.query_one("#learning-facts", DataTable)
        facts.add_columns("fact", "snapshot", "target", "status", "created")
        context = self.query_one("#learning-context", DataTable)
        context.add_columns("lesson", "assessment", "task", "verified outcome", "created")
        self.action_refresh()

    def on_unmount(self) -> None:
        """Invalidate a queued worker delivery before Textual prunes this screen's children."""
        self._generation += 1
        self._loading = False

    def action_back(self) -> None:
        self.dismiss()

    def action_refresh(self) -> bool:
        """Start exactly one explicit SQLite load. A busy press cannot replace a good view."""
        if self._loading:
            return False
        self._loading = True
        self._generation += 1
        generation = self._generation
        self.query_one("#learning-status", Static).update("Loading persisted learning lifecycle…")
        self._load_worker(generation)
        return True

    @work(thread=True)
    def _load_worker(self, generation: int) -> None:
        try:
            snapshot = self._data.learning_snapshot()
        except ObservatoryStoreUnavailable as exc:
            self.app.call_from_thread(self._finish_error, generation, str(exc))
            return
        self.app.call_from_thread(self._finish_ok, generation, snapshot)

    def _can_update_children(self) -> bool:
        return self.is_mounted and not self._pruning

    def _finish_ok(self, generation: int, snapshot: ObservatoryLearningSnapshot) -> None:
        if generation != self._generation:
            return
        self._loading = False
        if not self._can_update_children():
            return
        self._snapshot = snapshot
        self._render_snapshot(snapshot)
        self.query_one("#learning-status", Static).update("Persisted learning lifecycle")
        self._set_message("")

    def _finish_error(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._loading = False
        if not self._can_update_children():
            return
        # Keep any prior tables intact. An empty first load remains explicitly empty, never "cold".
        self.query_one("#learning-status", Static).update("Learning lifecycle unavailable")
        self._set_message(f"Learning store unavailable — {message}")

    def _render_snapshot(self, snapshot: ObservatoryLearningSnapshot) -> None:
        snapshots = self.query_one("#learning-snapshots", DataTable)
        facts = self.query_one("#learning-facts", DataTable)
        context = self.query_one("#learning-context", DataTable)
        snapshots.clear()
        facts.clear()
        context.clear()
        for item in snapshot.snapshots:
            snapshots.add_row(*_snapshot_row(item))
        for item in snapshot.facts:
            facts.add_row(*_fact_row(item))
        lesson_items = snapshot.learning_context.get("items", [])
        for item in lesson_items if isinstance(lesson_items, list) else []:
            context.add_row(*_context_row(item))
        self._set_empty("#learning-snapshots-empty", _EMPTY_SNAPSHOTS, not snapshot.snapshots)
        self._set_empty("#learning-facts-empty", _EMPTY_FACTS, not snapshot.facts)
        context_status = snapshot.learning_context.get("status")
        context_empty = _UNAVAILABLE_CONTEXT if context_status == "unavailable" else _EMPTY_CONTEXT
        self._set_empty("#learning-context-empty", context_empty, not lesson_items)

    def _set_empty(self, selector: str, text: str, visible: bool) -> None:
        widget = self.query_one(selector, Static)
        widget.update(text if visible else "")
        widget.display = visible

    def _set_message(self, text: str) -> None:
        widget = self.query_one("#learning-message", Static)
        widget.update(text)
        widget.display = bool(text)
