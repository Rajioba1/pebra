"""Phase-1 exit criterion (Architecture §14 Phase 1): assess -> edit -> verify loop works and an
out-of-envelope diff is caught. Runs the real CLI against a real temp git repo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "examples" / "login_patch.json"
_VENV_WIN = REPO / ".venv" / "Scripts" / "python.exe"
_VENV_POSIX = REPO / ".venv" / "bin" / "python"
PY = str(_VENV_WIN if _VENV_WIN.exists() else _VENV_POSIX if _VENV_POSIX.exists() else Path(sys.executable))
REQUIRED_CHECK = "run targeted tests for the touched scope before commit"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _pebra(cwd, *args):
    return subprocess.run(
        [PY, "-m", "pebra", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )


def _init_repo(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def validate_login(u, p):\n    return True\n",
                                              encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "src/auth.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_assess_then_verify_in_envelope_proceeds(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    # in-envelope edit: modify the approved file, stage it, leave HEAD unchanged (fresh evidence)
    (repo / "src" / "auth.py").write_text("def validate_login(u, p):\n    return bool(u and p)\n",
                                          encoding="utf-8")
    _git(repo, "add", "src/auth.py")

    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "staged",
               "--repo-root", str(repo), "--completed-check", f"{REQUIRED_CHECK}=passed")
    assert v.returncode == 0, v.stderr
    assert "PEBRA Verify: Proceed" in v.stdout


def test_completed_outcome_requires_passing_verify_then_succeeds(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    blocked = _pebra(
        repo, "record-outcome", "--assessment-id", "asm_1", "--status", "completed",
        "--repo-root", str(repo),
    )
    assert blocked.returncode == 2
    assert "requires a latest passing pebra verify" in blocked.stderr

    (repo / "src" / "auth.py").write_text(
        "def validate_login(u, p):\n    return bool(u and p)\n", encoding="utf-8"
    )
    _git(repo, "add", "src/auth.py")
    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "staged",
               "--repo-root", str(repo), "--completed-check", f"{REQUIRED_CHECK}=passed")
    assert v.returncode == 0, v.stderr

    recorded = _pebra(
        repo, "record-outcome", "--assessment-id", "asm_1", "--status", "completed",
        "--repo-root", str(repo),
    )
    assert recorded.returncode == 0, recorded.stderr


def test_assess_then_verify_out_of_envelope_is_caught(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    # out-of-envelope edit: touch a file the approved scope never mentioned
    (repo / "src" / "payments.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "src/payments.py")

    # pass the required check so scope-drift (inspect_first) is the isolated headline decision
    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "staged",
               "--repo-root", str(repo), "--completed-check", f"{REQUIRED_CHECK}=passed")
    assert v.returncode == 2, v.stdout
    assert "Inspect First" in v.stdout
    assert "Scope Drift:     yes" in v.stdout
    assert "payments.py" in v.stdout


def test_verify_catches_severity_escalation_via_reclassification(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    # in-SCOPE edit but a SIGNATURE change: pre-edit packet said BEHAVIORAL, actual is CONTRACT.
    # Post-edit reclassification must catch the severity escalation even though the file is allowed.
    (repo / "src" / "auth.py").write_text(
        "def validate_login(u, p, mfa):\n    return True\n", encoding="utf-8"
    )
    _git(repo, "add", "src/auth.py")

    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "staged",
               "--repo-root", str(repo), "--completed-check", f"{REQUIRED_CHECK}=passed")
    assert v.returncode == 2, v.stdout
    assert "Symbol Mismatch: yes" in v.stdout
    assert "Ask Human" in v.stdout


def test_syntax_error_diff_invalidates_bound_sanction(tmp_path) -> None:
    # AD-26: if the actual diff can't even be reclassified (syntax error), a sanction bound to the
    # assessment must be invalidated — PEBRA can't prove the approved profile still holds.
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    spec = repo / "sanction.json"
    spec.write_text(
        json.dumps({"risk_profile": "rp_1", "assessment_id": "asm_1",
                    "pre_edit_authorization_controls_satisfied": True}),
        encoding="utf-8",
    )
    s = _pebra(repo, "accept-risk", str(spec), "--repo-root", str(repo))
    assert s.returncode == 0, s.stderr

    # break the in-scope file so reclassification fails (UNKNOWN) without scope drift
    (repo / "src" / "auth.py").write_text("def validate_login(:\n", encoding="utf-8")
    _git(repo, "add", "src/auth.py")

    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "staged",
               "--repo-root", str(repo), "--completed-check", f"{REQUIRED_CHECK}=passed")
    assert v.returncode == 2, v.stdout
    assert "invalidated" in v.stdout


def test_verify_detects_stale_evidence_after_commit(tmp_path) -> None:
    repo = _init_repo(tmp_path)
    a = _pebra(repo, "assess", str(FIXTURE), "--repo-root", str(repo))
    assert a.returncode == 0, a.stderr

    # commit an in-scope change so HEAD moves past the assessed commit -> stale evidence
    (repo / "src" / "auth.py").write_text("def validate_login(u, p):\n    return bool(u)\n",
                                          encoding="utf-8")
    _git(repo, "add", "src/auth.py")
    _git(repo, "commit", "-q", "-m", "edit")

    v = _pebra(repo, "verify", "--assessment-id", "asm_1", "--scope", "all", "--repo-root", str(repo))
    assert v.returncode == 2, v.stdout
    assert "stale" in v.stdout
