"""Tests for the trend sparklines (Observatory TUI M4)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")

from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402
from pebra.tui.widgets.score_sparklines import trend_summary, trend_values  # noqa: E402


def test_trend_values_are_chronological_and_finite() -> None:
    series = [  # newest-first, as the store returns
        {"scores": {"rau": 0.3}},
        {"scores": {"rau": None}},
        {"scores": {"rau": float("nan")}},
        {"scores": {"rau": 0.1}},
    ]
    assert trend_values(series, "rau") == [0.1, 0.3]  # oldest -> newest, None/NaN dropped


def test_trend_values_tolerate_empty_and_null_scores() -> None:
    assert trend_values([], "rau") == []
    assert trend_values([{"scores": None}], "rau") == []


def test_trend_summary_reports_now_min_max_without_axis_claim() -> None:
    assert trend_summary([0.1, 0.3, -0.2]) == "now -0.20   min -0.20   max +0.30"
    assert trend_summary([]) == "—"


def _seed(tmp_path, *, rows: int = 3) -> str:
    from pebra.adapters.store.db import SqliteStore
    from pebra.core.constants import ActionStatus, Decision, RiskMode
    from pebra.core.models import AssessmentResult

    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    for i in range(rows):
        store.persist_assessment(
            AssessmentResult(
                recommended_decision=Decision.PROCEED,
                requires_confirmation=False,
                action_status=ActionStatus.PENDING,
                risk_mode=RiskMode.NORMAL,
                scores={"rau": 0.1 * i, "expected_loss": 0.02 * i, "benefit": 0.3 + 0.1 * i},
                repo_id="r",
                repo_root="/x",
                model_guidance_packet={"decision": "proceed"},
                assessed_commit=f"c{i:06d}",
            ),
            {"task": "t"},
        )
    store.close()
    return db


def test_sparklines_populate_from_snapshot(tmp_path) -> None:
    from textual.widgets import Sparkline

    db = _seed(tmp_path, rows=3)

    async def scenario() -> None:
        app = ObservatoryApp(
            ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)
        )
        async with app.run_test():
            assert list(app.query_one("#spark-rau", Sparkline).data or []) == [0.0, 0.1, 0.2]
            assert app.query_one("#spark-benefit", Sparkline).data

    asyncio.run(scenario())


def test_sparklines_blank_on_empty_history(tmp_path) -> None:
    from textual.widgets import Sparkline

    db = _seed(tmp_path, rows=0)

    async def scenario() -> None:
        app = ObservatoryApp(
            ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)
        )
        async with app.run_test():
            assert not app.query_one("#spark-rau", Sparkline).data  # None or empty, no crash

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(80, 24), (110, 30)])
def test_populated_ledger_keeps_three_trend_rows_in_viewport(tmp_path, size) -> None:
    from textual.widgets import DataTable, Label, Sparkline

    db = _seed(tmp_path, rows=100)

    async def scenario() -> None:
        app = ObservatoryApp(
            ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True)
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            table = app.query_one("#ledger", DataTable)
            trends = app.query_one("#trends")

            assert table.row_count == 100
            assert table.virtual_size.height > table.region.height > 0
            assert trends.region.height == 3
            assert screen.region.contains_region(trends.region)

            for key in ("rau", "expected_loss", "benefit"):
                label = app.query_one(f"#spark-{key}").parent.query_one(".trend-label", Label)
                spark = app.query_one(f"#spark-{key}", Sparkline)
                summary = app.query_one(f"#summary-{key}", Label)
                row = spark.parent
                assert row.region.height == 1
                assert screen.region.contains_region(label.region)
                assert screen.region.contains_region(summary.region)
                assert spark.region.width > 0
                assert label.region.right == spark.region.x
                assert spark.region.right == summary.region.x

    asyncio.run(scenario())
