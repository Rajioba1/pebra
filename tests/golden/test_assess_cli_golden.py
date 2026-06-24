"""Phase-0 milestone golden test (plan §5 / Architecture §14 Phase 0, Appendix A).

`python -m pebra assess examples/login_patch.json` prints the human card reproducing the worked
example (EL 0.10, risk budget 50%, EU 0.39, RAU 0.31, confidence 0.83, proceed w/ confirmation),
with core/ stdlib-only. Runs the real CLI as a subprocess in a temp repo (no real-repo writes).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "examples" / "login_patch.json"
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable


def _run(args, cwd):
    return subprocess.run(
        [PY, "-m", "pebra", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
    )


def test_assess_prints_human_card(tmp_path, snapshot) -> None:
    proc = _run(["assess", str(FIXTURE)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == snapshot


def test_assess_card_contains_worked_example_facts(tmp_path) -> None:
    proc = _run(["assess", str(FIXTURE)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Proceed (confirmation required)" in out
    assert "Moderate" in out  # Risk Level band
    assert "50%" in out  # risk budget
    assert "High (83%)" in out  # confidence
    assert "Positive" in out  # value after risk
    assert "0.10" in out  # expected damage
    # the raw "RAU" acronym must never appear in the human card
    assert "RAU" not in out


def test_assess_json_reproduces_numbers(tmp_path) -> None:
    proc = _run(["assess", str(FIXTURE), "--json"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    s = payload["scores"]
    assert payload["recommended_decision"] == "proceed"
    assert payload["requires_confirmation"] is True
    assert payload["risk_mode"] == "sensitive_context"
    assert round(s["expected_loss"], 2) == 0.10
    assert round(s["expected_utility"], 2) == 0.39
    assert round(s["utility_sd"], 2) == 0.06
    assert round(s["rau"], 2) == 0.31
    assert round(s["edit_confidence"], 2) == 0.83
    assert round(s["risk_budget_used"], 2) == 0.50
