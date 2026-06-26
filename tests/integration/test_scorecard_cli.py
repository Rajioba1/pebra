"""Milestone 4e — the full shadow measurement loop over the real CLI:
assess -> record-outcome (with labels) -> learn -> scorecard. Runs in a dep-light subprocess (the
golden venv), proving `pebra learn`/`scorecard` never pull a heavy/optional dep."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pebra.adapters.store.db import SqliteStore

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "examples" / "login_patch.json"
_VENV = REPO / ".venv" / "Scripts" / "python.exe"
PY = str(_VENV) if _VENV.exists() else sys.executable


def _pebra(cwd, *args):
    return subprocess.run(
        [PY, "-m", "pebra", *args], cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )


def test_scorecard_pending_on_empty_db(tmp_path) -> None:
    proc = _pebra(tmp_path, "scorecard", "--repo-root", str(tmp_path), "--db", str(tmp_path / "p.db"))
    assert proc.returncode == 0, proc.stderr
    assert "pending_min_n" in proc.stdout
    assert "Shadow rows recorded" in proc.stdout


def test_full_shadow_loop(tmp_path) -> None:
    db = str(tmp_path / "p.db")
    a = _pebra(tmp_path, "assess", str(FIXTURE), "--repo-root", str(tmp_path), "--db", db)
    assert a.returncode == 0, a.stderr
    store = SqliteStore(db)
    store.persist_guardrails("asm_1", {"pre_commit_decision": "proceed", "reasons": []})
    store.close()

    labels = json.dumps({
        "actual_success": True,
        "event_outcomes": {"test_regression": False, "security_sensitive_change": False},
        "benefit_realized": True,
    })
    r = _pebra(tmp_path, "record-outcome", "--assessment-id", "asm_1", "--status", "completed",
               "--detail", labels, "--repo-root", str(tmp_path), "--db", db)
    assert r.returncode == 0, r.stderr

    learn = _pebra(tmp_path, "learn", "--assessment-id", "asm_1", "--json",
                   "--repo-root", str(tmp_path), "--db", db)
    assert learn.returncode == 0, learn.stderr
    out = json.loads(learn.stdout)
    assert out["mode"] == "shadow measurement only; no decision parameters changed"
    assert out["observed"] >= 3            # p_success + 2 labeled events + benefit_realized
    assert out["prediction_errors"] == 6   # the full worked-example manifest

    sc = _pebra(tmp_path, "scorecard", "--json", "--repo-root", str(tmp_path), "--db", db)
    assert sc.returncode == 0, sc.stderr
    card = json.loads(sc.stdout)
    assert card["calibration"]["risk_binary"]["status"] == "ok"      # observed risk labels -> metrics
    assert card["calibration"]["risk_binary"]["brier"] >= 0.0
    assert card["shadow_counts"]["prediction_errors"] == 6


def test_learn_without_outcome_exits_nonzero(tmp_path) -> None:
    db = str(tmp_path / "p.db")
    a = _pebra(tmp_path, "assess", str(FIXTURE), "--repo-root", str(tmp_path), "--db", db)
    assert a.returncode == 0, a.stderr
    learn = _pebra(tmp_path, "learn", "--assessment-id", "asm_1", "--repo-root", str(tmp_path), "--db", db)
    assert learn.returncode == 2
    assert "no terminal outcome" in learn.stderr
