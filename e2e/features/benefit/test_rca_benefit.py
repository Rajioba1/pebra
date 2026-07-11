"""RCA benefit e2e — maps the new multi-language benefit signal over the real CLI boundary.

CLI-only (``python -m pebra ...`` via cli_harness; NO ``import pebra``). Proves, end-to-end:
  * assess: a complexity-REDUCING edit yields a higher ``scores.benefit`` than a complexity-INCREASING
    edit (Python AND TypeScript — the latter is impossible under the old radon provider);
  * fail-safe: an unsupported language (.kt) earns no maintainability credit and never crashes
    (deterministic, needs NO binary);
  * verify: ``pebra verify --json`` exposes the measured RCA deltas end-to-end (the wire, not dashboard).

These fixtures supply a small explicit exposure (which always WINS over graph-derived exposure) on
purpose — it keeps this lane graph-independent and fast (no CodeGraph index needed) while proving the
RCA measurement→benefit path over the CLI. The RCA adapter itself never invents exposure; graph-derived
default exposure is covered in controller/merge tests where trusted graph fan-in can be injected.
"""

from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

from e2e.utils import cli_harness as ch

# before (committed), simpler (complexity down), complexer (complexity up) — Python + TypeScript pairs.
_PY_BEFORE = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"
_PY_SIMPLER = "def f(x):\n    return abs(x)\n"
_PY_COMPLEXER = "def f(x):\n    if x > 0:\n        if x > 10:\n            return x * 2\n        return x\n    return -x\n"

_TS_BEFORE = "export function f(x: number) { if (x > 0) return x; return -x }\n"
_TS_SIMPLER = "export function f(x: number) { return Math.abs(x) }\n"
_TS_COMPLEXER = "export function f(x: number) { if (x > 0) { if (x > 10) return x * 2; return x } return -x }\n"

_KT_BEFORE = "fun f(x: Int): Int { return if (x > 0) x else -x }\n"
_KT_SIMPLER = "fun f(x: Int): Int = Math.abs(x)\n"
_KT_COMPLEXER = "fun f(x: Int): Int { if (x > 0) { if (x > 10) return x * 2; return x }; return -x }\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _repo(dest: Path, filename: str, content: str) -> Path:
    return _repo_files(dest, {filename: content})


def _repo_files(dest: Path, files: dict[str, str]) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        path = dest / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(dest, "init", "-q")
    _git(dest, "config", "user.email", "e2e@pebra.test")
    _git(dest, "config", "user.name", "pebra-e2e")
    _git(dest, "add", ".")
    _git(dest, "commit", "-q", "-m", "seed")
    return dest


def _patch(filename: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=filename, tofile=filename))


def _write_request(path: Path, repo_id: str, filename: str, patch: str, *, exposure: float) -> Path:
    return _write_multifile_request(
        path, repo_id, [filename], patch, exposure=exposure
    )


def _write_multifile_request(
    path: Path, repo_id: str, filenames: list[str], patch: str, *, exposure: float
) -> Path:
    request = {
        "schema_version": "0.1",
        "task": "rca benefit e2e",
        "repo_id": repo_id,
        "candidate_actions": [{
            "id": "a1", "label": "edit", "action_type": "edit",
            "expected_files": filenames, "proposed_patch": patch,
        }],
        "evidence": {
            "p_success": 0.9,
            "immediate_benefit": 0.3,
            "review_cost": 0.1,
            "criticality_stage": "C1",
            "criticality_value": 0.2,
            # explicit, small: this is caller policy, NOT something RCA invents (see module docstring).
            "benefit_delta_evidence": {
                "source_type": "projected", "future_change_exposure": exposure, "deltas": {},
            },
        },
    }
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def _benefit(repo: Path, db: Path, req: Path) -> float:
    return ch.assess(req, repo_root=repo, db=db)["scores"]["benefit"]


def _assess_direction(tmp_path: Path, filename: str, before: str, simpler: str, complexer: str) -> None:
    """Same file/request, only the patch differs: the simpler edit must score a HIGHER benefit."""
    repo = _repo(tmp_path / "repo", filename, before)
    db = tmp_path / "p.db"
    simpler_req = _write_request(
        tmp_path / "s.json", "rca_e2e", filename, _patch(filename, before, simpler), exposure=0.1)
    complexer_req = _write_request(
        tmp_path / "c.json", "rca_e2e", filename, _patch(filename, before, complexer), exposure=0.1)
    assert _benefit(repo, db, simpler_req) > _benefit(repo, db, complexer_req)


def test_assess_benefit_higher_for_simpler_python(require_rca, tmp_path) -> None:
    _assess_direction(tmp_path, "calc.py", _PY_BEFORE, _PY_SIMPLER, _PY_COMPLEXER)


def test_assess_benefit_moves_for_typescript(require_rca, tmp_path) -> None:
    # The RCA-over-radon upgrade: benefit now moves for a NON-Python file over the CLI. radon never could.
    _assess_direction(tmp_path, "calc.ts", _TS_BEFORE, _TS_SIMPLER, _TS_COMPLEXER)


def test_assess_failsafe_unsupported_language_no_credit(tmp_path) -> None:
    # UNGATED (no require_rca): .kt is filtered by extension BEFORE the binary, so this holds with or
    # without RCA installed. Assess completes and benefit is identical for the simpler/complexer patches.
    repo = _repo(tmp_path / "repo", "calc.kt", _KT_BEFORE)
    db = tmp_path / "p.db"
    simpler = _write_request(
        tmp_path / "s.json", "rca_e2e", "calc.kt", _patch("calc.kt", _KT_BEFORE, _KT_SIMPLER), exposure=0.1)
    complexer = _write_request(
        tmp_path / "c.json", "rca_e2e", "calc.kt", _patch("calc.kt", _KT_BEFORE, _KT_COMPLEXER), exposure=0.1)
    assert _benefit(repo, db, simpler) == 0.3  # no fabricated credit beyond immediate_benefit
    assert _benefit(repo, db, complexer) == 0.3


def test_verify_json_exposes_measured_benefit_for_simplification(require_rca, tmp_path) -> None:
    # THE wire: assess -> apply a real simplification -> `verify --json` surfaces the measured RCA deltas
    # (not dashboard-only). HEAD(complex) -> staged(simpler) => complexity down, MI up, benefit positive.
    repo = _repo(tmp_path / "repo", "calc.py", _PY_BEFORE)
    db = tmp_path / "p.db"
    req = _write_request(
        tmp_path / "r.json", "rca_e2e", "calc.py", _patch("calc.py", _PY_BEFORE, _PY_SIMPLER), exposure=0.1)
    asm = ch.assess(req, repo_root=repo, db=db)["assessment_id"]

    (repo / "calc.py").write_text(_PY_SIMPLER, encoding="utf-8")  # apply the edit for real
    _git(repo, "add", "calc.py")

    _passed, payload = ch.verify(asm, repo_root=repo, db=db, scope="staged")
    # The RCA deltas are exposed on the verify JSON regardless of the envelope decision (the wire is the
    # point; PROCEED/escalate is a separate concern driven by risk, not benefit).
    deltas = payload["measured_benefit_deltas"]
    assert deltas["complexity_delta"] < 0            # fewer branches
    assert deltas["maintainability_index_delta"] > 0  # more maintainable
    assert payload["measured_benefit"] > 0


def test_multilanguage_multifile_benefit_survives_assess_and_verify(require_rca, tmp_path) -> None:
    repo = _repo_files(
        tmp_path / "repo",
        {"calc.py": _PY_BEFORE, "calc.ts": _TS_BEFORE},
    )
    patch = _patch("calc.py", _PY_BEFORE, _PY_SIMPLER) + _patch(
        "calc.ts", _TS_BEFORE, _TS_SIMPLER
    )
    db = tmp_path / "p.db"
    req = _write_multifile_request(
        tmp_path / "r.json",
        "rca_multifile_e2e",
        ["calc.py", "calc.ts"],
        patch,
        exposure=0.1,
    )

    assessed = ch.assess(req, repo_root=repo, db=db)
    aggregate = assessed["scores"]["candidate_aggregate"]
    breakdown = assessed["scores"]["benefit_breakdown"]
    assert aggregate["file_count"] == 2
    assert breakdown["source_type"] == "measured"
    assert set(assessed["scores"]["benefit_file_deltas"]) == {"calc.py", "calc.ts"}
    assert all(
        values["complexity_delta"] < 0
        for values in assessed["scores"]["benefit_file_deltas"].values()
    )
    assert all(
        values["maintainability_index_delta"] > 0
        for values in assessed["scores"]["benefit_file_deltas"].values()
    )

    (repo / "calc.py").write_text(_PY_SIMPLER, encoding="utf-8")
    (repo / "calc.ts").write_text(_TS_SIMPLER, encoding="utf-8")
    _git(repo, "add", "calc.py", "calc.ts")

    _passed, verified = ch.verify(
        assessed["assessment_id"], repo_root=repo, db=db, scope="staged"
    )
    deltas = verified["measured_benefit_deltas"]
    assert deltas["complexity_delta"] < 0
    assert deltas["maintainability_index_delta"] > 0
    assert verified["measured_benefit"] > 0
