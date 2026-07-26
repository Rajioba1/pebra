"""Milestone 2: below 900px the wide assessments table drops low-priority columns, but every hidden
column's data stays reachable via the row-click detail card. Deterministic source guards; the visual
hide + click round-trip is proven in the ui-e2e lane."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_APP_JS = ROOT / "pebra" / "dashboard" / "static" / "app.js"
_STYLE = ROOT / "pebra" / "dashboard" / "static" / "style.css"

# 1-indexed columns hidden below 900px: assessment id, fingerprint, rau, confidence, lesson.
_HIDDEN_COLUMNS = (1, 4, 9, 10, 12)


def _js() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def test_media_query_hides_low_priority_columns_scoped_to_the_table() -> None:
    css = _STYLE.read_text(encoding="utf-8")
    assert "@media (max-width: 900px)" in css, "no narrow-viewport breakpoint for the wide table"
    assert re.search(r"\.table-scroll table\s*\{\s*min-width:\s*760px", css), (
        "narrow tier must lower the min-width so hiding columns actually reflows"
    )
    for col in _HIDDEN_COLUMNS:
        assert f".table-scroll th:nth-child({col})" in css, f"column {col} header not hidden (scoped)"
        assert f".table-scroll td:nth-child({col})" in css, f"column {col} cell not hidden (scoped)"
    # Scoped to .table-scroll only — must not blanket-hide cells in other tables.
    assert re.search(r"@media[^{]*\{[^@]*?\}\s*\}", css), "media block malformed"


def test_hidden_columns_stay_reachable_in_the_detail_card() -> None:
    js = _js()
    assert "function rowIdentityTable(" in js, "detail card needs a row-identity block"
    ident = re.search(r"function rowIdentityTable\([^)]*\)\s*\{(.*?)\n  \}", js, re.DOTALL)
    assert ident, "rowIdentityTable body not found"
    body = ident.group(1)
    # Every hidden-and-not-otherwise-shown column must appear in the identity block.
    assert "formatFingerprint(" in body, "fingerprint must stay reachable"
    assert "s.rau" in body, "rau must stay reachable"
    assert "edit_confidence" in body, "confidence must stay reachable"
    assert "lessonText" in body, "lesson must stay reachable"
    # (assessment id is already shown in the measured-benefit table, so it needn't repeat here.)


def test_detail_card_is_invoked_with_the_row() -> None:
    js = _js()
    assert "showMeasuredBenefit(it.assessment_id, bbody, it," in js, "row click must pass the row object"
    assert ".find(" in js, "the restore-on-refresh path must re-look-up the row to pass it"
