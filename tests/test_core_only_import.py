"""Architecture §4.4 — the engine imports with zero adapters present (nox `core-only` intent).

Importing the full core engine in a FRESH interpreter must not pull in any pebra.adapters / pebra.app
/ surface module. Run as a subprocess so the assertion is not polluted by other tests that import
adapters in-process. This is the in-process analogue of the `nox -s core-only` session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

_SNIPPET = """
import importlib, sys
mods = [
    "pebra.core.constants", "pebra.core.models", "pebra.core.score_math",
    "pebra.core.benefit_model", "pebra.core.score_normalizer", "pebra.core.weight_resolver",
    "pebra.core.confidence_gate", "pebra.core.change_classifier", "pebra.core.request_validator",
    "pebra.core.candidate_parser", "pebra.core.assessment_builder", "pebra.core.decision_engine",
    "pebra.core.explanation_generator", "pebra.core.model_guidance", "pebra.core.high_risk_controls",
]
for m in mods:
    importlib.import_module(m)
leaked = [n for n in sys.modules if n.startswith(
    ("pebra.adapters", "pebra.app", "pebra.cli", "pebra.mcp_server", "pebra.dashboard"))]
assert leaked == [], "core import leaked: " + repr(leaked)
print("CORE_ONLY_OK")
"""


def test_core_imports_pull_in_no_adapters_or_surfaces() -> None:
    proc = subprocess.run(
        [PY, "-c", _SNIPPET],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "CORE_ONLY_OK" in proc.stdout
