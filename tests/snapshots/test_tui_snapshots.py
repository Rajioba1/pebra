"""SVG snapshot tests for the Observatory ledger screen (Observatory TUI M3).

These lock the rendered surface — the RAU-lane, the colored decisions (the color encoding is under visual
regression here), the status line, the empty state, and the chain-failure banner — across narrow/wide
terminals and dark/light themes. Regenerate baselines with `pytest --snapshot-update` after an
intentional visual change, then review the SVGs before committing.
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")
pytest.importorskip("pytest_textual_snapshot", reason="requires pytest-textual-snapshot (run via nox)")

from pebra.adapters.store.db import SqliteStore  # noqa: E402
from pebra.core.constants import ActionStatus, Decision, RiskMode  # noqa: E402
from pebra.core.models import AssessmentResult  # noqa: E402
from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402

# A fixed, decision-diverse ledger. The inspect_first row has a POSITIVE rau (+0.15) yet is held — the
# case that proves the RAU lane (position) and the decision (color/glyph) are independent channels.
_SPECS = [
    (Decision.PROCEED, "aaaa111", {"rau": 0.21, "expected_loss": 0.05, "benefit": 0.55}),
    (Decision.INSPECT_FIRST, "bbbb222", {"rau": 0.15, "expected_loss": 0.09, "benefit": 0.40}),
    (Decision.ASK_HUMAN, "cccc333", {"rau": -0.14, "expected_loss": 0.15, "benefit": 0.53}),
    (Decision.REJECT, "dddd444", {"rau": -0.31, "expected_loss": 0.36, "benefit": 0.20}),
]


@pytest.fixture(autouse=True)
def _color_snapshots_are_environment_independent(monkeypatch) -> None:
    """Color is part of these baselines, even when the invoking shell sets NO_COLOR."""
    monkeypatch.delenv("NO_COLOR", raising=False)


def _seed(tmp_path, *, specs=_SPECS, break_chain: bool = False) -> str:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    for decision, commit, scores in specs:
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
    if break_chain:
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE assessments SET row_hash = ? WHERE id = (SELECT MIN(id) FROM assessments)",
            ("0" * 64,),
        )
        con.commit()
        con.close()
    return db


def _app(db: str) -> ObservatoryApp:
    return ObservatoryApp(ObservatoryContext(db_path=db, repo_id="r", repo_root=None, read_only=True))


def test_snapshot_ledger_mixed_decisions_wide_dark(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(110, 30))


def test_snapshot_ledger_narrow(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(80, 24))


def test_snapshot_ledger_light_theme(snap_compare, tmp_path) -> None:
    # Start in the light theme so the single on-mount load renders the light palette (no second reload).
    app = _app(_seed(tmp_path))
    app.theme = "textual-light"
    assert snap_compare(app, terminal_size=(110, 30))


def test_snapshot_empty_history(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path, specs=[])), terminal_size=(90, 20))


def test_snapshot_chain_failure(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path, break_chain=True)), terminal_size=(100, 24))


async def _open_first_detail(pilot) -> None:
    from textual.widgets import DataTable

    pilot.app.query_one("#ledger", DataTable).focus()
    await pilot.press("enter")
    await pilot.pause()


def test_snapshot_detail_screen(snap_compare, tmp_path) -> None:
    assert snap_compare(
        _app(_seed(tmp_path)), terminal_size=(110, 36), run_before=_open_first_detail
    )
