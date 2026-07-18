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
            assert "2 assessments" in status
            message = app.query_one("#ledger-message")
            assert message.display is False  # no empty-state message when rows exist

    asyncio.run(scenario())


def test_content_columns_preserve_the_full_lane_and_decision_label(tmp_path) -> None:
    from textual.widgets import DataTable

    from pebra.tui.widgets.ledger_table import render_rau_lane

    db = _seed(tmp_path, rows=2)

    async def scenario() -> None:
        app = ObservatoryApp(_ctx_for(db))
        async with app.run_test():
            columns = list(app.query_one("#ledger", DataTable).columns.values())
            lane, decision = columns[2], columns[3]
            assert lane.auto_width is False
            assert lane.width >= len(render_rau_lane(0.0))
            assert decision.auto_width is False
            assert decision.width >= len("◇ Inspect first")

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


def test_status_line_is_store_scoped_and_pure() -> None:
    from pebra.tui.widgets.status_header import format_status

    line = format_status(repo_id="r", latest_commit="abcdef123456", chain_valid=True, total=1)
    assert "repo r" in line
    assert "HEAD abcdef1" in line  # short commit
    assert "store chain ok" in line
    assert "1 assessment" in line and "assessments" not in line  # singular


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
