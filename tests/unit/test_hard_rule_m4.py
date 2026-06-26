"""Milestone 4 Hard Rule: PEBRA may MEASURE learning, but must not REAPPLY it to decisions.

Belt-and-suspenders beyond the `assess-no-learning` import-linter contract: a static check that the
assess path neither imports the learning modules nor sets a non-None active_snapshot (which would be
the only way learned facts could enter scoring — that is Milestone 5).
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


def test_assess_controller_active_snapshot_stays_none() -> None:
    # the AssessmentInput(...) the controller builds must pass active_snapshot=None — no learned
    # snapshot is ever applied in the live path (Milestone 5 territory).
    tree = ast.parse(_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "active_snapshot":
                    found = True
                    assert isinstance(kw.value, ast.Constant) and kw.value.value is None
    assert found, "expected an explicit active_snapshot=None in the assess path"
