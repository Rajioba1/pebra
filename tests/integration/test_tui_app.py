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
            {"task": "t"},
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
        async with app.run_test():
            columns = list(app.query_one("#ledger", DataTable).columns.values())
            lane, decision = columns[2], columns[3]
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


def test_ledger_fits_eighty_columns_and_pins_asm_id(tmp_path) -> None:
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            table = app.query_one("#ledger", DataTable)
            assert table.fixed_columns == 1  # asm-id column pinned across horizontal scroll
            assert table.max_scroll_x == 0  # all eight columns fit 80 cols — no scroll needed
            assert app.query_one("#scroll-hint").display is False

    asyncio.run(scenario())


def test_narrow_terminal_reveals_scroll_hint(tmp_path) -> None:
    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(70, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.query_one("#ledger", DataTable).max_scroll_x > 0  # overflow at 70 cols
            assert app.query_one("#scroll-hint").display is True

    asyncio.run(scenario())


def test_snapshot_width_growth_reveals_scroll_hint_without_a_resize(tmp_path) -> None:
    from dataclasses import replace

    from textual.widgets import DataTable

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test(size=(80, 24)) as pilot:
            # Drain the initial resize/layout callbacks so a pending startup update cannot mask the
            # behavior under test.
            for _ in range(5):
                await pilot.pause()
            table = app.query_one("#ledger", DataTable)
            assert table.max_scroll_x == 0
            assert app.query_one("#scroll-hint").display is False

            snapshot = app.screen._data.refresh_snapshot()
            long_id = "asm_" + ("9" * 24)
            wider_rows = [{**snapshot.assessments[0], "assessment_id": long_id}]
            app.screen._apply_snapshot(replace(snapshot, assessments=wider_rows))
            for _ in range(3):
                await pilot.pause()

            assert table.max_scroll_x > 0
            assert app.query_one("#scroll-hint").display is True

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
    assert "HEAD abcdef1" in line  # short commit
    assert "store chain ok" in line  # "store" kept — the chain is database-global, not repo-scoped
    assert "1 asm" in line
    # a real repo_id compacts to a short slug that fits a narrow pane
    compact = format_status(
        repo_id="repo_481a73928338", latest_commit="cc5d175abc", chain_valid=True, total=16
    )
    assert "repo 481a7392 " in compact  # "repo_" dropped, first 8 hex kept
    assert len(compact) < 60  # fits a ~70-column pane without wrapping


def test_theme_change_recolors_existing_decision_cells_without_reloading(tmp_path) -> None:
    from textual.coordinate import Coordinate
    from textual.widgets import DataTable

    from pebra.tui.widgets.ledger_table import decision_cell

    db = _seed(tmp_path, rows=1)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test() as pilot:
            table = app.query_one("#ledger", DataTable)
            dark_cell = table.get_cell_at(Coordinate(0, 3))
            assert dark_cell.spans[0].style == decision_cell("proceed", dark=True).spans[0].style
            app.theme = "textual-light"
            await pilot.pause()
            light_cell = table.get_cell_at(Coordinate(0, 3))
            assert light_cell.spans[0].style == decision_cell("proceed", dark=False).spans[0].style

    asyncio.run(scenario())
