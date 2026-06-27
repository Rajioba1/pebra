"""Hard Rule (evolved at M5c): the assess path may READ the active learned snapshot and apply it
pre-scoring (pure ``apply_snapshot``), but must perform NO learning WRITE and import no learning-writer
module. Static guards below; the runtime "no write on the assess path" check lives in
tests/unit/test_snapshot_read_wiring.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ASSESS = Path(__file__).resolve().parents[2] / "pebra" / "app" / "assess_controller.py"


def _source() -> str:
    return _ASSESS.read_text(encoding="utf-8")


def test_assess_controller_does_not_import_learning() -> None:
    tree = ast.parse(_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported.extend(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    banned = ("learning_controller", "prediction_error", "learning_store", "calibration_store")
    assert not any(any(b in name for b in banned) for name in imported), imported


def test_no_hardcoded_active_snapshot_none() -> None:
    # the Phase-0 stub `active_snapshot=None` in the AssessmentInput(...) constructor is gone; M5c
    # assigns active_snapshot from the read-port result (inp.active_snapshot = bundle) instead.
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg != "active_snapshot", "active_snapshot must not be hardcoded in a call"


def test_apply_snapshot_is_imported_on_assess_path() -> None:
    # the read-path is wired: apply_snapshot is imported (pure, read-only reapplication — no write).
    tree = ast.parse(_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.extend(f"{node.module}.{a.name}" for a in node.names)
    assert "pebra.core.apply_snapshot.apply_snapshot" in imported
