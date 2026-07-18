"""Tests for the Observatory command palette (Observatory TUI M4) — read-only commands only."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402


def _seed(tmp_path) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    for decision in (Decision.PROCEED, Decision.ASK_HUMAN):
        store.persist_assessment(
            AssessmentResult(
                recommended_decision=decision,
                requires_confirmation=decision is not Decision.PROCEED,
                action_status=ActionStatus.PENDING,
                risk_mode=RiskMode.NORMAL,
                scores={"rau": 0.1},
                repo_id="r",
                repo_root="/x",
                model_guidance_packet={"decision": decision.value},
                assessed_commit="c0",
            ),
            {"task": "t"},
        )
    store.close()
    return db


def _ctx(db: str) -> ObservatoryContext:
    return ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)


def test_system_commands_include_read_only_observatory_commands(tmp_path) -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx(_seed(tmp_path)))
        async with app.run_test():
            titles = {command.title for command in app.get_system_commands(app.screen)}
            assert {"Refresh", "Overview", "Help"} <= titles
            # sanity: a mutating verb never appears
            assert not any(
                word in title.lower()
                for title in titles
                for word in ("apply", "accept", "delete", "write", "commit")
            )

    asyncio.run(scenario())


def test_overview_summary_reports_counts(tmp_path) -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx(_seed(tmp_path)))
        async with app.run_test():
            summary = app.screen.overview_summary()
            assert "2 assessments" in summary
            assert "proceed 1" in summary and "ask_human 1" in summary

    asyncio.run(scenario())


def test_refresh_and_overview_commands_notify(tmp_path) -> None:
    async def scenario() -> None:
        app = ObservatoryApp(_ctx(_seed(tmp_path)))
        notes: list[str] = []
        async with app.run_test():
            app.notify = lambda message, **_kw: notes.append(message)  # type: ignore[assignment]
            app._command_overview()
            app._command_refresh()
            assert any("assessments" in n for n in notes)
            assert any("Refreshing" in n for n in notes)

    asyncio.run(scenario())