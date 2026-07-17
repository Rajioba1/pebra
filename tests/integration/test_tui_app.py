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
