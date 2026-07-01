"""The compiler-outcome scenario flow — real build truth feeding the learning loop, over the CLI.

Made LOAD-BEARING (review C2): 99 seeded cycles are NOT enough to promote (< MIN_CALIBRATION_SAMPLES);
the ONE real build cycle — whose outcome label comes from the actual compiler (`actual_success = dotnet
build passed`) — is the 100th sample that tips the gate. So ``promoted_pre`` must be False and the final
promotion must fire BECAUSE of the real cycle, not the seeds. No pebra import (boundary rule).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import signature_edit as se
from e2e.utils import cli_harness as ch

SEED_N = 99  # +1 real cycle = 100 == MIN_CALIBRATION_SAMPLES (so the real cycle is the tipping sample)


@dataclass
class CompilerOutcomeState:
    baseline_decision: str
    baseline_rau: float
    baseline_build_passed: bool
    dotnet_available: bool
    build_ran: bool
    build_passed: bool
    build_errors: str
    promoted_pre: bool          # promotion outcome with ONLY the 99 seeds (must be False)
    promotion: dict             # promotion AFTER the real cycle's outcome is recorded (must fire)
    observed_risk_rows: int     # scorecard observed risk_binary rows (real DB state, not a constant)
    learned_decision: str
    learned_rau: float
    applied_snapshot_id: str | None
    real_build_cycles: int
    seeded_cycles: int


def _checks(payload: dict) -> list[str]:
    return list(payload["model_guidance_packet"]["binding"].get("required_checks_before_commit", []))


def _staged_files(copy: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(copy), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _apply_verify_record_failed(
    *,
    copy: Path,
    db: Path | str,
    req_path: Path,
    build_label: bool,
) -> tuple[dict, dn.DotNetBuildResult | None]:
    """One honest lifecycle: clean tree -> pre-edit assess -> apply -> verify -> record -> learn."""
    se.reset_signature_change(copy)
    assessed = ch.assess(req_path, repo_root=copy, db=db)
    se.apply_signature_change(copy)
    assert _staged_files(copy) == [se.IWORKSPACE_REL]
    passed, _ = ch.verify(
        assessed["assessment_id"], repo_root=copy, db=db, completed_checks=_checks(assessed)
    )
    assert passed, "verify must PROCEED before recording a completed outcome"
    build = dn.run_build(copy) if build_label else None
    detail = {"actual_success": False}
    if build is not None:
        detail = {"actual_success": build.passed, "build_exit_code": build.exit_code}
    ch.record_outcome(assessed["assessment_id"], "completed", repo_root=copy, db=db, detail=detail)
    ch.learn(assessed["assessment_id"], repo_root=copy, db=db)
    se.reset_signature_change(copy)
    return assessed, build


def build_compiler_outcome_state(copy_path: Path | str, db: Path | str) -> CompilerOutcomeState:
    copy = Path(copy_path)
    req_path = copy.parent / "cca_request.json"
    req_path.write_text(json.dumps(se.build_signature_request(copy)), encoding="utf-8")
    followup_path = copy.parent / "cca_followup.json"
    followup_path.write_text(json.dumps(se.build_followup_request(copy)), encoding="utf-8")

    se.reset_signature_change(copy)
    baseline = ch.assess(req_path, repo_root=copy, db=db)
    baseline_build = dn.run_build(copy)
    assert baseline_build.passed, baseline_build.error_summary

    # --- 99 SEEDED cycles (authored failures) — deliberately one short of the promotion gate. Each is
    # still a true pre-edit lifecycle; only the outcome label is authored instead of compiler-derived.
    seeded_cycles = 0
    for _ in range(SEED_N):
        _apply_verify_record_failed(copy=copy, db=db, req_path=req_path, build_label=False)
        seeded_cycles += 1

    promo_pre = ch.promote(repo_root=copy, db=db)  # must NOT fire: 99 < MIN_CALIBRATION_SAMPLES

    # --- the 1 REAL build cycle: its outcome is the COMPILER'S verdict and the 100th calibration row ---
    _real_assessed, build = _apply_verify_record_failed(
        copy=copy, db=db, req_path=req_path, build_label=True
    )
    assert build is not None

    promotion = ch.promote(repo_root=copy, db=db)  # now fires — the real cycle tipped the gate
    scorecard = ch.scorecard(repo_root=copy, db=db)
    se.reset_signature_change(copy)  # restore clean tree for the future-proposal reassess
    learned = ch.assess(followup_path, repo_root=copy, db=db)
    applied = learned.get("applied_snapshot_provenance") or {}

    return CompilerOutcomeState(
        baseline_decision=baseline["recommended_decision"], baseline_rau=baseline["scores"]["rau"],
        baseline_build_passed=baseline_build.passed,
        dotnet_available=build.available, build_ran=build.ran, build_passed=build.passed,
        build_errors=build.error_summary,
        promoted_pre=bool(promo_pre["risk"]["promoted"]), promotion=promotion,
        observed_risk_rows=int(scorecard["calibration"]["risk_binary"].get("n", 0)),
        learned_decision=learned["recommended_decision"], learned_rau=learned["scores"]["rau"],
        applied_snapshot_id=applied.get("snapshot_id"),
        real_build_cycles=1, seeded_cycles=seeded_cycles,
    )
