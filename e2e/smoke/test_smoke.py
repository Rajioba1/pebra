"""Phase E1 smoke — fast health check that the CLI boundary works end to end (one assess + one
record-outcome on the real risky repo). If this fails, no feature test can pass; run it first."""

from __future__ import annotations

from e2e.utils import cli_harness as ch

_DECISIONS = {"proceed", "inspect_first", "test_first", "ask_human", "reject"}


def test_smoke_assess_then_record_outcome(risky_repo, e2e_db, request_json_path):
    payload = ch.assess(request_json_path, repo_root=risky_repo, db=e2e_db)
    assert payload["assessment_id"]
    assert payload["recommended_decision"] in _DECISIONS
    assert 0.0 <= payload["scores"]["edit_confidence"] <= 1.0
    # the outcome path must accept the captured id (proves record-outcome wiring over the boundary).
    # 'skipped' is the no-verify-required terminal status — keeps the smoke fast (the full
    # completed-needs-verify cycle is exercised by the agent feature test).
    ch.record_outcome(
        payload["assessment_id"], "skipped", repo_root=risky_repo, db=e2e_db,
        detail={"actual_success": True},
    )
