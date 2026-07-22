"""Integration tests for the minimal ObservatoryApp shell (Observatory TUI M2).

Shell only: Header + Footer, packaged TCSS, merged theme variables, q/Ctrl+Q to quit — no database read
yet. Driven via App.run_test()/Pilot, wrapped in asyncio.run so no pytest-asyncio dependency is needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from pebra.observatory_context import ObservatoryContext
from pebra.tui.app import ObservatoryApp


def _ctx() -> ObservatoryContext:
    return ObservatoryContext(db_path="x.db", repo_id="r", repo_root=None, read_only=True)


def test_shell_composes_header_footer_title_and_quits_on_q() -> None:
    from textual.widgets import Footer, Header

    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test() as pilot:
            assert app.query_one(Header) is not None
            assert app.query_one(Footer) is not None
            assert app.title == "PEBRA Observatory"
            await pilot.press("q")
        assert app.return_code == 0  # 0 only if the quit action fired (teardown leaves it None)

    asyncio.run(scenario())


def test_ctrl_q_also_quits() -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
        assert app.return_code == 0

    asyncio.run(scenario())


def test_ctrl_q_remains_the_inherited_priority_binding() -> None:
    from textual.app import App

    assert all(binding[0] != "ctrl+q" for binding in ObservatoryApp.BINDINGS)
    inherited = next(binding for binding in App.BINDINGS if binding.key == "ctrl+q")
    assert inherited.priority is True


def test_question_mark_footer_binding_toggles_textual_help_panel() -> None:
    from textual.widgets import HelpPanel

    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test() as pilot:
            binding = app.active_bindings["question_mark"].binding
            assert binding.action == "show_help_panel"
            assert binding.description == "pebra --help"
            assert len(app.query(HelpPanel)) == 0
            assert "escape" not in app.active_bindings
            await pilot.press("?")
            await pilot.pause()
            assert app.query_one(HelpPanel) is not None
            escape = app.active_bindings["escape"].binding
            assert escape.action == "close_help_panel"
            assert escape.description == "Back"
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.query(HelpPanel)) == 0
            assert "escape" not in app.active_bindings
            await pilot.press("?")
            await pilot.pause()
            assert app.query_one(HelpPanel) is not None
            await pilot.press("?")
            await pilot.pause()
            assert len(app.query(HelpPanel)) == 0
            await pilot.press("?")
            await pilot.pause()
            assert app.query_one(HelpPanel) is not None

    asyncio.run(scenario())


def test_get_css_variables_merges_custom_without_dropping_builtins() -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test():
            merged = app.get_css_variables()
            assert merged["observatory-ink"] == "#0F1216"
            assert "background" in merged  # a Textual builtin design token must survive the merge

    asyncio.run(scenario())


def test_uses_packaged_tcss_asset() -> None:
    path = Path(ObservatoryApp.CSS_PATH)
    assert path.name == "theme.tcss"
    assert path.is_file()


# --- M3: the ledger screen over a real store ---


def _seed(tmp_path, *, rows: int = 2) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    specs = [
        (Decision.PROCEED, "aaaa111", {"rau": 0.14, "expected_loss": 0.05, "benefit": 0.52}),
        (Decision.ASK_HUMAN, "bbbb222", {"rau": -0.14, "expected_loss": 0.15, "benefit": 0.53}),
    ]
    for decision, commit, scores in specs[:rows]:
        store.persist_assessment(
            AssessmentResult(
                recommended_decision=decision,
                requires_confirmation=decision is not Decision.PROCEED,
                action_status=ActionStatus.PENDING,
                risk_mode=RiskMode.NORMAL,
                scores=scores,
                repo_id="r",
                repo_root="/x",
                model_guidance_packet={"decision": decision.value},
                assessed_commit=commit,
            ),
            {
                "task": "Fix failing login validation without changing session behavior",
                "action_id": "edit-login",
                "revision_envelope": {"expected_files": ["src/auth.py", "tests/test_auth.py"]},
            },
        )
    store.close()
    return db


def _ctx_for(db: str) -> ObservatoryContext:
    return ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)


def test_screen_renders_seeded_ledger_and_status(tmp_path) -> None:
    from textual.widgets import DataTable

    from pebra.tui.widgets.status_header import StatusHeader

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test():
            table = app.query_one("#ledger", DataTable)
            assert table.row_count == 2
            status = app.query_one("#status", StatusHeader).status_text
            assert "store chain ok" in status
            assert "2 asm" in status
            message = app.query_one("#ledger-message")
            assert message.display is False  # no empty-state message when rows exist

    asyncio.run(scenario())


def test_content_columns_preserve_the_full_lane_and_decision_label(tmp_path) -> None:
    from textual.widgets import DataTable

    from pebra.tui.widgets.ledger_table import LEDGER_LANE_WIDTH, render_rau_lane

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(120, 30)):
            columns = list(app.query_one("#ledger", DataTable).columns.values())
            lane, decision = columns[11], columns[2]
            assert lane.auto_width is False
            assert lane.width == LEDGER_LANE_WIDTH == len(render_rau_lane(0.0, width=LEDGER_LANE_WIDTH))
            assert decision.auto_width is False
            assert decision.width >= len("◇ Inspect first")

    asyncio.run(scenario())


def test_banner_is_responsive_to_terminal_size(tmp_path) -> None:
    from pebra.tui.widgets.banner import PebraBanner

    db = _seed(tmp_path, rows=1)

    async def visibility(width: int, height: int) -> tuple[bool, int]:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            await pilot.pause()
            banner = app.query_one("#banner", PebraBanner)
            return banner.display, banner.region.height

    async def scenario() -> None:
        assert await visibility(110, 30) == (True, 2)  # wide + tall: wordmark + tagline
        assert await visibility(90, 30) == (True, 1)   # medium: wordmark line only
        shown, _ = await visibility(80, 24)
        assert shown is False  # short terminal (incl. common 80x24): hidden
        shown, _ = await visibility(70, 30)
        assert shown is False  # narrow terminal: hidden

    asyncio.run(scenario())


def test_reduced_motion_settles_the_banner_without_a_reveal(tmp_path) -> None:
    from pebra.tui.widgets.banner import PebraBanner

    db = _seed(tmp_path, rows=1)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        app.animation_level = "none"  # simulate TEXTUAL_ANIMATIONS=none
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            banner = app.query_one("#banner", PebraBanner)
            assert banner._reveal_timer is None  # no sweep scheduled
            assert "*" not in banner.render().plain  # already at the settled, marker-free frame

    asyncio.run(scenario())


def test_banner_reveal_is_one_pass_and_survives_refresh(tmp_path) -> None:
    from pebra.tui.widgets.banner import _REST_INDEX, PebraBanner

    db = _seed(tmp_path, rows=1)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(110, 30)) as pilot:
            banner = app.query_one("#banner", PebraBanner)
            banner.settle()  # force the reveal to its final frame deterministically
            await pilot.pause()
            assert banner._reveal_timer is None
            assert banner._marker == _REST_INDEX
            # a refresh must NOT restart the reveal
            app.screen.reload()
            await pilot.pause()
            assert banner._reveal_timer is None
            assert banner._marker == _REST_INDEX

    asyncio.run(scenario())


def test_header_subtitle_shows_source_provenance() -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test():
            assert app.sub_title
            assert ("editable" in app.sub_title) or ("installed" in app.sub_title)

    asyncio.run(scenario())


def _column_labels(table) -> list[str]:
    return [column.label.plain for column in table.columns.values()]


@pytest.mark.parametrize("width", [70, 80, 100, 120])
def test_ledger_uses_locked_complete_columns_at_every_width(tmp_path, width: int) -> None:
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#ledger", DataTable)
            assert table.fixed_columns == 1
            assert _column_labels(table) == [
                "ID", "target", "decision", "RAU", "loss", "benefit", "status", "prior",
                "lesson", "task", "assessed commit", "gate lane", "assessed time",
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [70, 80, 100, 120])
def test_every_width_requests_the_complete_semantic_column_set(tmp_path, width: int) -> None:
    """Milestone 0 forward spec for Milestone 1: no width silently drops instrument fields; narrow
    terminals scroll instead. The full ordered instrument is locked here."""
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)
    captured: dict[str, list[str]] = {}

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#ledger", DataTable)
            assert table.fixed_columns == 1
            captured["labels"] = [c.lower() for c in _column_labels(table)]

    asyncio.run(scenario())
    assert tuple(captured["labels"]) == (
        "id", "target", "decision", "rau", "loss", "benefit", "status", "prior", "lesson", "task",
        "assessed commit", "gate lane", "assessed time",
    )


def test_narrow_ledger_scrolls_to_last_column_without_changing_selection(tmp_path) -> None:
    """Milestone 0 forward spec for Milestone 1: at 70 columns the full instrument overflows and the
    last column is reachable by horizontal scroll, and scrolling never changes the selected row."""
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=3)
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(70, 24)) as pilot:
            table = app.query_one("#ledger", DataTable)
            table.focus()
            table.move_cursor(row=1, column=0, scroll=False)
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            await pilot.pause()
            result["overflows"] = table.max_scroll_x > 0
            await pilot.press("end")  # jump to the horizontal end (row-cursor mode -> scroll_x=max)
            await pilot.pause()
            result["reached_end"] = table.scroll_x == table.max_scroll_x
            last_cell = table.get_cell_at((1, 12))
            last_column = list(table.columns.values())[-1]
            result["last_cell_full"] = (
                len(last_cell.plain) == 16 and last_column.width >= len(last_cell.plain)
            )
            still = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            result["selection_preserved"] = still == selected

    asyncio.run(scenario())
    assert result["overflows"] is True
    assert result["reached_end"] is True
    assert result["last_cell_full"] is True
    assert result["selection_preserved"] is True


def test_horizontal_scroll_survives_a_data_refresh(tmp_path) -> None:
    """Milestone 0 forward spec for Milestone 1: a 5s/manual data refresh must not reset horizontal
    scroll (columns are installed once, not rebuilt). Requires the complete overflowing column set."""
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=3)
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(70, 24)) as pilot:
            table = app.query_one("#ledger", DataTable)
            table.focus()
            await pilot.pause()
            await pilot.press("end")  # scroll to the horizontal end
            await pilot.pause()
            before = table.scroll_x
            await pilot.press("r")  # manual refresh
            await pilot.pause()
            result["before"] = before
            result["overflowed"] = before > 0
            result["after"] = table.scroll_x

    asyncio.run(scenario())
    assert result["overflowed"] is True
    assert result["after"] == result["before"]  # refresh preserved horizontal scroll


def test_resize_preserves_selected_assessment(tmp_path) -> None:
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#ledger", DataTable)
            table.focus()
            table.move_cursor(row=1, column=3, scroll=False)
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            await pilot.resize_terminal(70, 24)
            await pilot.pause()
            assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == selected
            assert table.has_focus

    asyncio.run(scenario())


def test_task_and_target_cells_render_brackets_literally_through_textual(tmp_path) -> None:
    from textual.widgets import DataTable
    from textual.widgets._data_table import default_cell_formatter

    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "literal-cells.db")
    store = SqliteStore(db)
    store.persist_assessment(
        AssessmentResult(
            recommended_decision=Decision.PROCEED,
            requires_confirmation=False,
            action_status=ActionStatus.PENDING,
            risk_mode=RiskMode.NORMAL,
            scores={"rau": 0.31},
            repo_id="r",
            repo_root="/x",
            assessed_commit="abc1234",
        ),
        {
            "task": "refactor Dict[str, Any] and [bold]",
            "revision_envelope": {"expected_files": ["src/[bold].py"]},
        },
    )
    store.close()

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(120, 30)):
            table = app.query_one("#ledger", DataTable)
            target = default_cell_formatter(table.get_cell_at((0, 1)))
            task = default_cell_formatter(table.get_cell_at((0, 9)))
            assert target.plain == "[bold].py"
            assert task.plain == "refactor Dict[str, Any] and…"

    asyncio.run(scenario())


def test_resize_preserves_horizontal_scroll(tmp_path) -> None:
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(120, 24)) as pilot:
            table = app.query_one("#ledger", DataTable)
            for _ in range(3):
                await pilot.pause()
            table.scroll_to(x=20, animate=False, force=True, immediate=True)
            await pilot.pause()
            assert table.scroll_x > 0
            await pilot.resize_terminal(100, 24)
            await pilot.pause()
            assert table.scroll_x == 20

    asyncio.run(scenario())


def test_resize_preserves_interaction_state_with_offscreen_selection(tmp_path) -> None:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult
    from textual.widgets import DataTable

    db = str(tmp_path / "many.db")
    store = SqliteStore(db)
    for index in range(30):
        store.persist_assessment(
            AssessmentResult(
                recommended_decision=Decision.PROCEED,
                requires_confirmation=False,
                action_status=ActionStatus.PENDING,
                risk_mode=RiskMode.NORMAL,
                scores={"rau": 0.1},
                repo_id="r",
                repo_root="/x",
                assessed_commit=f"{index:07d}",
            ),
            {
                "task": "Edit authentication",
                "revision_envelope": {"expected_files": ["src/auth.py"]},
            },
        )
    store.close()

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(120, 18)) as pilot:
            table = app.query_one("#ledger", DataTable)
            for _ in range(3):
                await pilot.pause()
            table.focus()
            table.move_cursor(row=0, column=5, scroll=False)
            table.scroll_to(x=12, y=12, animate=False, force=True, immediate=True)
            await pilot.pause()
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            old_scroll_y = table.scroll_y
            assert old_scroll_y > 0

            await pilot.resize_terminal(100, 18)
            for _ in range(3):
                await pilot.pause()

            assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == selected
            assert table.scroll_x == 12
            assert table.scroll_y == old_scroll_y
            assert table.has_focus

    asyncio.run(scenario())


def test_snapshot_width_growth_reveals_scroll_hint_without_a_resize(tmp_path) -> None:
    from dataclasses import replace

    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(240, 24)) as pilot:
            # Drain the initial resize/layout callbacks so a pending startup update cannot mask the
            # behavior under test.
            for _ in range(5):
                await pilot.pause()
            table = app.query_one("#ledger", DataTable)
            assert table.max_scroll_x == 0
            assert app.query_one("#scroll-hint").display is False

            snapshot = app.screen._data.refresh_snapshot()
            long_id = "asm_" + ("9" * 256)
            wider_rows = [{**snapshot.assessments[0], "assessment_id": long_id}]
            app.screen._apply_snapshot(replace(snapshot, assessments=wider_rows))
            for _ in range(3):
                await pilot.pause()

            assert table.max_scroll_x > 0
            hint = app.query_one("#scroll-hint")
            assert hint.display is True
            assert hint.render().plain == "←/→ columns · Home/End edges"

    asyncio.run(scenario())


def test_empty_repo_shows_empty_state(tmp_path) -> None:
    from textual.widgets import DataTable, Static

    db = _seed(tmp_path, rows=0)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test():
            assert app.query_one("#ledger", DataTable).row_count == 0
            message = app.query_one("#ledger-message", Static)
            assert message.display is True
            assert "No assessments" in app.screen.message_text

    asyncio.run(scenario())


def test_unavailable_store_shows_durable_error(tmp_path) -> None:
    from textual.widgets import Static

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(str(tmp_path / "does-not-exist.db")))
        async with app.run_test():
            message = app.query_one("#ledger-message", Static)
            assert message.display is True
            assert "store unavailable" in app.screen.message_text.lower()

    asyncio.run(scenario())


def test_status_line_is_store_scoped_compact_and_pure() -> None:
    from pebra.tui.widgets.status_header import format_status

    line = format_status(repo_id="r", latest_commit="abcdef123456", chain_valid=True, total=1)
    assert "repo r" in line
    assert "latest assessed abcdef1" in line
    assert "HEAD" not in line
    assert "store chain ok" in line  # "store" kept — the chain is database-global, not repo-scoped
    assert "1 asm" in line
    # a real repo_id compacts to a short slug that fits a narrow pane
    compact = format_status(
        repo_id="repo_481a73928338", latest_commit="cc5d175abc", chain_valid=True, total=16
    )
    assert "repo 481a7392 " in compact  # "repo_" dropped, first 8 hex kept
    assert len(compact) <= 70  # fits the locked narrow terminal without wrapping


def test_status_calls_commit_latest_assessed_not_head() -> None:
    from pebra.tui.widgets.status_header import format_status

    line = format_status(repo_id="r", latest_commit="abcdef123", chain_valid=True, total=3)
    assert line == "repo r · latest assessed abcdef1 · store chain ok · 3 asm"
    assert "HEAD" not in line


def test_theme_change_recolors_existing_decision_cells_without_reloading(tmp_path) -> None:
    from textual.coordinate import Coordinate
    from textual.widgets import DataTable

    from pebra.tui.widgets.ledger_table import decision_cell

    db = _seed(tmp_path, rows=1)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test() as pilot:
            table = app.query_one("#ledger", DataTable)
            dark_cell = table.get_cell_at(Coordinate(0, 2))
            assert dark_cell.spans[0].style == decision_cell("proceed", dark=True).spans[0].style
            app.theme = "textual-light"
            await pilot.pause()
            light_cell = table.get_cell_at(Coordinate(0, 2))
            assert light_cell.spans[0].style == decision_cell("proceed", dark=False).spans[0].style

    asyncio.run(scenario())


def test_grouping_toggle_is_raw_by_default_dynamic_and_reversible() -> None:
    from textual.app import App
    from textual.coordinate import Coordinate
    from textual.screen import Screen
    from textual.widgets import DataTable, Static

    from pebra.tui.data import ObservatorySnapshot
    from pebra.tui.screens.observatory import ObservatoryScreen

    def row(assessment_id: str, fingerprint: str) -> dict:
        return {
            "assessment_id": assessment_id,
            "candidate_fingerprint": fingerprint,
            "decision": "proceed",
            "assessed_commit": "abc1234",
            "terminal_status": None,
            "task": "Fix authentication",
            "action_id": "edit-auth",
            "target_files": ["src/auth.py"],
            "scores": {"rau": 0.2, "expected_loss": 0.1, "benefit": 0.3},
        }

    rows = [row("asm_3", "a" * 64), row("asm_2", "a" * 64), row("asm_1", "b" * 64)]

    class _Data:
        repo_id = "r"

        def refresh_snapshot(self) -> ObservatorySnapshot:
            return ObservatorySnapshot(
                overview={"total": 3, "by_decision": {"proceed": 3}},
                assessments=rows,
                scores_series=[{"rau": 0.2}],
                chain={"valid": True},
            )

        def detail(self, assessment_id: str) -> dict:
            return {}

    class _Harness(App):
        def get_default_screen(self) -> Screen:
            return ObservatoryScreen(_Data())

    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(100, 24)) as pilot:
            screen = app.screen
            table = app.query_one("#ledger", DataTable)
            caption = app.query_one("#ledger-caption", Static)
            assert screen.group_repeats is False
            assert table.row_count == 3
            assert caption.display is False
            assert app.active_bindings["g"].binding.description == "Group repeats"

            table.focus()
            table.move_cursor(row=1, scroll=False)
            assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == "asm_2"
            await pilot.press("g")
            await pilot.pause()

            assert screen.group_repeats is True
            assert table.row_count == 2
            assert table.get_cell_at(Coordinate(0, 0)) == "asm_3 ×2"
            assert "2 groups / 3 assessments" in str(caption.render())
            assert app.active_bindings["g"].binding.description == "Show raw"

            await pilot.press("g")
            await pilot.pause()

            assert screen.group_repeats is False
            assert table.row_count == 3
            assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == "asm_2"
            assert caption.display is False
            assert app.active_bindings["g"].binding.description == "Group repeats"

    asyncio.run(scenario())


def test_grouping_keeps_overview_and_trends_on_raw_assessments() -> None:
    from textual.app import App
    from textual.screen import Screen

    from pebra.tui.data import ObservatorySnapshot
    from pebra.tui.screens.observatory import ObservatoryScreen
    from pebra.tui.widgets.score_sparklines import ScoreSparklines

    rows = [
        {
            "assessment_id": assessment_id,
            "candidate_fingerprint": "a" * 64,
            "decision": "proceed",
            "assessed_commit": "abc1234",
            "terminal_status": None,
            "task": "Fix authentication",
            "action_id": "edit-auth",
            "target_files": ["src/auth.py"],
            "scores": {"rau": 0.2, "expected_loss": 0.1, "benefit": 0.3},
        }
        for assessment_id in ("asm_2", "asm_1")
    ]
    series = [{"scores": {"rau": 0.2}}, {"scores": {"rau": 0.2}}]

    class _Data:
        repo_id = "r"

        def refresh_snapshot(self) -> ObservatorySnapshot:
            return ObservatorySnapshot(
                overview={"total": 2, "by_decision": {"proceed": 2}},
                assessments=rows,
                scores_series=series,
                chain={"valid": True},
            )

        def detail(self, assessment_id: str) -> dict:
            return {}

    class _Harness(App):
        def get_default_screen(self) -> Screen:
            return ObservatoryScreen(_Data())

    async def scenario() -> None:
        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            assert app.screen.group_repeats is True
            assert app.screen.overview_summary() == "2 assessments — proceed 2"
            assert app.screen._rows == rows
            assert list(app.query_one("#trends", ScoreSparklines).query_one("#spark-rau").data) == [
                0.2,
                0.2,
            ]

    asyncio.run(scenario())
