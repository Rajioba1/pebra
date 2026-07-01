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

from e2e.external.utils import diagnostic_parser as dp
from e2e.external.utils import dotnet_harness as dn
from e2e.external.utils import graph_resolver as gr
from e2e.external.utils import signature_edit as se
from e2e.utils import cli_harness as ch

SEED_N = 99  # +1 real cycle = 100 == MIN_CALIBRATION_SAMPLES (so the real cycle is the tipping sample)

# The edited interface method, in the form the CodeGraph resolver matches (qualified_name IWorkspace.CanCloseAsync).
EDITED_SYMBOL = "IWorkspace::CanCloseAsync"
# The compiler codes that PROVE the predicted public_api_break materialized (an implementer/caller broke),
# vs a mere syntax error in the changed file. Only these flip the LEARNED event label — and the event we
# record is the one the request actually predicts (public_api_break, see signature_edit.py), so it joins
# to a real calibration target in prediction_error._risk_actual rather than being an orphan detail key.
_PUBLIC_API_BREAK_CODES = {"CS0535", "CS7036"}


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


@dataclass
class CompilerAttributionState:
    """Phase 1 attribution: the real build cycle enriched with graph attribution (provenance only)."""
    baseline_build_passed: bool
    delta_diagnostic_count: int
    delta_codes: tuple[str, ...]
    graph_attribution: dict | None
    attribution_method: str
    attribution_confidence: float
    implements_edge: bool
    predicted_callers: int
    actual_broken_files: int
    unresolved_count: int
    event_outcomes_recorded: dict
    graph_freshness: str
    assess_has_attribution_key: bool  # governance: attribution must NOT appear in the assess payload
    promoted_pre: bool
    promotion: dict
    observed_risk_rows: int


def _predicted_callers(assessed: dict) -> int:
    """The pre-edit fan-in PEBRA predicted for the edited symbol — the honest 'callers' number.

    0 means the graph supplied no trusted value at assess time (NOT 'zero callers')."""
    gp = assessed.get("graph_provenance") or {}
    sf = gp.get("symbol_fanin") or {}
    val = sf.get("caller_count")
    return int(val) if isinstance(val, int) and not isinstance(val, bool) else 0


def _apply_verify_record_with_attribution(
    *, copy: Path, db: Path | str, req_path: Path, baseline_keys, codegraph_db_path: Path,
) -> tuple[dict, dn.DotNetBuildResult, dict, dict, int, int]:
    """The real cycle, enriched: after the compiler's verdict, resolve the DELTA diagnostics to graph
    nodes and record a graph_attribution provenance blob + honest event_outcomes. Same pre-edit ordering
    as the plain cycle (assess -> apply -> verify -> build -> record -> learn)."""
    se.reset_signature_change(copy)
    assessed = ch.assess(req_path, repo_root=copy, db=db)
    predicted = _predicted_callers(assessed)
    se.apply_signature_change(copy)
    assert _staged_files(copy) == [se.IWORKSPACE_REL]
    passed, _ = ch.verify(
        assessed["assessment_id"], repo_root=copy, db=db, completed_checks=_checks(assessed)
    )
    assert passed, "verify must PROCEED before recording a completed outcome"

    build = dn.run_build_delta(copy, baseline_keys=baseline_keys)
    delta = build.delta_diagnostics
    results, unresolved = gr.resolve_diagnostics(delta, EDITED_SYMBOL, codegraph_db_path)
    blob = gr.assemble_graph_attribution(
        results, diags=delta, predicted_dependents=predicted, unresolved_count=unresolved
    )
    public_api_break = any(d.code in _PUBLIC_API_BREAK_CODES for d in delta)
    event_outcomes = {"public_api_break": True} if public_api_break else {}

    detail = {"actual_success": build.passed, "build_exit_code": build.exit_code,
              "graph_attribution": blob}
    if event_outcomes:
        detail["event_outcomes"] = event_outcomes
    ch.record_outcome(assessed["assessment_id"], "completed", repo_root=copy, db=db, detail=detail)
    ch.learn(assessed["assessment_id"], repo_root=copy, db=db)
    se.reset_signature_change(copy)
    return assessed, build, blob, event_outcomes, predicted, unresolved


def build_compiler_attribution_state(
    copy_path: Path | str, db: Path | str, *, codegraph_db_path: Path | str
) -> CompilerAttributionState:
    """Same 99-seed + 1-real load-bearing shape as the outcome lane, but the real cycle also produces a
    graph_attribution provenance blob. Attribution is EVIDENCE — it never touches a score (asserted by
    ``assess_has_attribution_key`` being False)."""
    copy = Path(copy_path)
    req_path = copy.parent / "cca_attr_request.json"
    req_path.write_text(json.dumps(se.build_signature_request(copy)), encoding="utf-8")

    se.reset_signature_change(copy)
    baseline_build = dn.run_build_delta(copy, baseline_keys=frozenset())
    assert baseline_build.passed, baseline_build.error_summary
    baseline_keys = dp.diagnostics_as_keyset(baseline_build.structured_diagnostics)

    for _ in range(SEED_N):
        _apply_verify_record_failed(copy=copy, db=db, req_path=req_path, build_label=False)

    promo_pre = ch.promote(repo_root=copy, db=db)  # must NOT fire at 99

    _assessed, build, blob, event_outcomes, _predicted, _unresolved = (
        _apply_verify_record_with_attribution(
            copy=copy, db=db, req_path=req_path, baseline_keys=baseline_keys,
            codegraph_db_path=Path(codegraph_db_path),
        )
    )
    promotion = ch.promote(repo_root=copy, db=db)  # now fires — the real cycle tipped the gate
    scorecard = ch.scorecard(repo_root=copy, db=db)
    se.reset_signature_change(copy)

    # GOVERNANCE: assess AFTER graph_attribution is in the outcome store. The scored payload must NOT echo
    # it back — a post-write reassess is the honest check (the pre-write one would pass trivially).
    reassessed = ch.assess(req_path, repo_root=copy, db=db)
    assess_has_attr = "graph_attribution" in reassessed

    return CompilerAttributionState(
        baseline_build_passed=baseline_build.passed,
        delta_diagnostic_count=len(build.delta_diagnostics),
        delta_codes=tuple(sorted({d.code for d in build.delta_diagnostics})),
        graph_attribution=blob,
        attribution_method=blob["attribution_method"],
        attribution_confidence=blob["attribution_confidence"],
        implements_edge=blob["implements_edge"],
        predicted_callers=blob["predicted_callers"],
        actual_broken_files=blob["actual_broken_files"],
        unresolved_count=blob["unresolved_count"],
        event_outcomes_recorded=event_outcomes,
        graph_freshness=blob["graph_freshness"],
        assess_has_attribution_key=assess_has_attr,
        promoted_pre=bool(promo_pre["risk"]["promoted"]),
        promotion=promotion,
        observed_risk_rows=int(scorecard["calibration"]["risk_binary"].get("n", 0)),
    )


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
