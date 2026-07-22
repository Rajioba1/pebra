r"""SVG snapshot tests for the Observatory ledger screen (Observatory TUI M3).

These lock the rendered surface — the RAU-lane, the colored decisions (the color encoding is under visual
regression here), the status line, the empty state, and the chain-failure banner — across narrow/wide
terminals and dark/light themes. Regenerate baselines with
`.\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update` after an intentional visual
change, then review the SVGs before committing.
"""

from __future__ import annotations

import datetime
import sqlite3
from types import SimpleNamespace

import pytest

pytest.importorskip("textual", reason="requires textual (run via nox)")
pytest.importorskip("pytest_textual_snapshot", reason="requires pytest-textual-snapshot (run via nox)")

import pytest_textual_snapshot  # noqa: E402

from pebra.adapters.store.db import SqliteStore  # noqa: E402
from pebra.core.constants import ActionStatus, Decision, RiskMode  # noqa: E402
from pebra.core.models import AssessmentResult  # noqa: E402
from pebra.observatory_context import ObservatoryContext  # noqa: E402
from pebra.tui.app import ObservatoryApp  # noqa: E402

# A fixed, decision-diverse ledger. The inspect_first row has a POSITIVE rau (+0.15) yet is held — the
# case that proves the RAU lane (position) and the decision (color/glyph) are independent channels.
_SPECS = [
    (Decision.PROCEED, "aaaa111", {"rau": 0.21, "expected_loss": 1.45, "benefit": 0.55}),
    (Decision.INSPECT_FIRST, "bbbb222", {"rau": 0.15, "expected_loss": 0.09, "benefit": 0.40}),
    (Decision.ASK_HUMAN, "cccc333", {"rau": -0.14, "expected_loss": 0.15, "benefit": 0.53}),
    (Decision.REJECT, "dddd444", {"rau": -0.31, "expected_loss": 0.36, "benefit": 0.20}),
]

# normalize_svg is a private plugin internal required by these color-sensitive baselines.
def _require_normalize_svg(plugin):
    try:
        return plugin.normalize_svg
    except AttributeError as exc:
        raise RuntimeError(
            "pytest-textual-snapshot==1.1.0 must provide the normalize_svg hook"
        ) from exc


_ORIGINAL_NORMALIZE_SVG = _require_normalize_svg(pytest_textual_snapshot)


def _normalize_snapshot_svg(svg: str) -> str:
    normalized = _ORIGINAL_NORMALIZE_SVG(svg)
    lines: list[str] = []
    for line in normalized.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        lines.append(content.rstrip() + newline)
    return "".join(lines)


@pytest.fixture(autouse=True)
def _color_snapshots_are_environment_independent(monkeypatch) -> None:
    """Color is part of these baselines, even when the invoking shell sets NO_COLOR. The banner's
    one-time reveal is also settled to its final frame so a mid-sweep frame can never make a baseline
    non-deterministic (TEXTUAL_ANIMATIONS is read at import time, so settle the reveal directly)."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(pytest_textual_snapshot, "normalize_svg", _normalize_snapshot_svg)

    from pebra.tui import app as app_mod
    from pebra.tui.widgets import banner as banner_mod
    from pebra.adapters.store import db as db_mod

    class _FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 20, 12, 34, 56, 123456, tzinfo=tz)

    # Provenance is intentionally environment-specific (installed/editable + git hash); keep the
    # visual baseline stable across local checkouts, CI, and future commits.
    monkeypatch.setattr(app_mod, "provenance_line", lambda *, prefix=True: "0.1.0 · test")
    monkeypatch.setattr(
        banner_mod.PebraBanner,
        "on_mount",
        lambda self: self.update(banner_mod.banner_content(banner_mod._REST_INDEX)),
    )
    monkeypatch.setattr(
        db_mod,
        "datetime",
        SimpleNamespace(datetime=_FixedDateTime, timezone=datetime.timezone),
    )


def test_snapshot_normalizer_strips_trailing_space_and_rich_terminal_ids() -> None:
    svg = '<svg class="terminal-123-matrix">  \n        \n</svg>\t\n'

    assert _normalize_snapshot_svg(svg) == '<svg class="terminal-matrix">\n\n</svg>\n'


def test_snapshot_normalizer_requires_supported_plugin_hook() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"pytest-textual-snapshot==1\.1\.0.*normalize_svg",
    ):
        _require_normalize_svg(SimpleNamespace())


def _seed(tmp_path, *, specs=_SPECS, break_chain: bool = False, groupable: bool = False) -> str:
    db = str(tmp_path / "pebra.db")
    store = SqliteStore(db)
    for decision, commit, scores in specs:
        assessment_id = store.persist_assessment(
            AssessmentResult(
                recommended_decision=decision,
                requires_confirmation=decision is not Decision.PROCEED,
                action_status=ActionStatus.PENDING,
                risk_mode=RiskMode.NORMAL,
                scores=scores,
                repo_id="r",
                repo_root="/x",
                model_guidance_packet={
                    "decision": decision.value,
                    **(
                        {
                            "binding": {
                                "candidate": {
                                    "algorithm": "sha256-normalized-content-v1",
                                    "files": {"src/auth.py": "a" * 64},
                                }
                            }
                        }
                        if groupable
                        else {}
                    ),
                },
                assessed_commit=commit,
            ),
            {
                "task": "Fix authentication validation without changing session behavior",
                "action_id": "edit-auth",
                "revision_envelope": {
                    "expected_files": ["src/auth.py", "tests/test_auth.py"]
                },
            },
        )
        if decision is Decision.PROCEED:
            store.record_outcome(assessment_id, ActionStatus.COMPLETED.value)
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


def _app(db: str, *, exploration: bool = False) -> ObservatoryApp:
    context = ObservatoryContext(
        db_path=db,
        repo_id="r",
        repo_root="/repo" if exploration else None,
        read_only=True,
    )
    return ObservatoryApp(
        context, explorer_factory=(lambda: object()) if exploration else None
    )


def test_snapshot_ledger_width_120(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(120, 30))


def test_snapshot_ledger_width_80(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(80, 24))


def test_snapshot_ledger_width_70(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(70, 24))


def test_snapshot_ledger_width_100(snap_compare, tmp_path) -> None:
    assert snap_compare(_app(_seed(tmp_path)), terminal_size=(100, 30))


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
        _app(_seed(tmp_path), exploration=True),
        terminal_size=(110, 36),
        run_before=_open_first_detail,
    )


async def _toggle_grouping(pilot) -> None:
    await pilot.press("g")
    await pilot.pause()


async def _open_learning(pilot) -> None:
    await pilot.press("l")
    for _ in range(3):
        await pilot.pause()


def test_snapshot_learning_empty_state(snap_compare, tmp_path) -> None:
    assert snap_compare(
        _app(_seed(tmp_path, specs=[])),
        terminal_size=(100, 30),
        run_before=_open_learning,
    )


def test_snapshot_grouped_ledger(snap_compare, tmp_path) -> None:
    repeated = [
        (Decision.PROCEED, "aaaa111", {"rau": 0.21, "expected_loss": 0.05, "benefit": 0.55}),
        (Decision.PROCEED, "aaaa111", {"rau": 0.21, "expected_loss": 0.05, "benefit": 0.55}),
        (Decision.REJECT, "dddd444", {"rau": -0.31, "expected_loss": 0.36, "benefit": 0.20}),
    ]
    assert snap_compare(
        _app(_seed(tmp_path, specs=repeated, groupable=True)),
        terminal_size=(100, 30),
        run_before=_toggle_grouping,
    )
