"""M5c — snapshot read-path wired into assess (the first live learning-driven decision change).

Cold-start (no active facts) -> identity, scores unchanged (golden safety). A seeded active fact ->
the decision moves, and the prediction manifest records the USED (overridden) p_success — not the raw
evidence value (the ratified capture-the-used-value rule). The assess path performs NO learning write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pebra import composition
from pebra.app import assess_controller
from pebra.adapters.store.db import GENESIS, _risk_snapshot_canonical, _row_hash
from pebra.core import candidate_parser

FIXTURE = Path(__file__).resolve().parents[1].parent / "examples" / "login_patch.json"


def _seed_active_p_success_fact(store, repo_id, *, value=0.10, sample_size=100):
    created_at = "2026-01-01T00:00:00Z"
    rs_content = _risk_snapshot_canonical(repo_id, "active", {}, created_at, {"hash_version": 2})
    rs_hash = _row_hash(GENESIS, rs_content)
    rs = store._con.execute(
        "INSERT INTO risk_snapshots (repo_id, status, metrics_json, hash_version, created_at, "
        "prev_hash, row_hash) VALUES (?, 'active', '{}', 2, ?, ?, ?)",
        (repo_id, created_at, GENESIS, rs_hash),
    ).lastrowid
    fact_json = json.dumps(
        {"value": value, "sample_size": sample_size, "calibration_method": "brier_bucket"}
    )
    fact_content = json.dumps(
        {
            "repo_id": repo_id,
            "snapshot_id": str(rs),
            "fact_type": "learned_override",
            "target_type": "risk_binary",
            "target_name": "p_success",
            "scope_kind": "global",
            "scope_value": "",
            "specificity_rank": 0,
            "scope": {},
            "fact": json.loads(fact_json),
            "status": "active",
            "requires_human_ratification": 0,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    fact_hash = _row_hash(GENESIS, fact_content)
    store._con.execute(
        "INSERT INTO learned_risk_facts (repo_id, snapshot_id, fact_type, target_type, target_name, "
        "scope_kind, scope_value, specificity_rank, scope_json, fact_json, status, "
        "requires_human_ratification, created_at, prev_hash, row_hash) "
        "VALUES (?, ?, 'learned_override', 'risk_binary', 'p_success', 'global', '', 0, '{}', ?, "
        "'active', 0, ?, ?, ?)",
        (repo_id, str(rs), fact_json, created_at, GENESIS, fact_hash),
    )


def _assess(tmp_path, *, seed=False):
    request = candidate_parser.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))
    ctx = composition.resolve_repo_and_db(str(tmp_path), str(tmp_path / "p.db"))
    if seed:
        _seed_active_p_success_fact(ctx.store, ctx.repo.repo_id)
    try:
        outcome = assess_controller.assess(
            request, thresholds=request.thresholds, start_path=str(tmp_path),
            **composition.build_assess_ports(request, ctx),
        )
        counts = ctx.store.chain_status()["counts"]
        predictions = ctx.store.load_predictions(outcome.assessment_id)
    finally:
        ctx.store.close()
    return outcome, counts, predictions


def test_no_active_facts_reproduces_worked_example(tmp_path) -> None:
    outcome, counts, _ = _assess(tmp_path, seed=False)
    s = outcome.recommended_result.scores
    assert round(s["rau"], 2) == 0.31 and round(s["edit_confidence"], 2) == 0.83  # identity
    assert counts["risk_snapshots"] == 0 and counts["prediction_errors"] == 0  # no learning write


def test_seeded_active_fact_moves_the_decision(tmp_path) -> None:
    baseline, _, _ = _assess(tmp_path / "base", seed=False)
    seeded, counts, predictions = _assess(tmp_path / "seed", seed=True)

    # p_success overridden 0.74 -> 0.10 lowers expected utility / RAU: a real, learning-driven change
    assert seeded.recommended_result.scores["rau"] != pytest.approx(baseline.recommended_result.scores["rau"])
    assert seeded.recommended_result.scores["expected_utility"] < baseline.recommended_result.scores["expected_utility"]

    # the prediction manifest records the USED (overridden) value, not the raw 0.74 (ratified rule)
    ps = next(p for p in predictions if p["target_name"] == "p_success")
    assert ps["predicted_value"] == 0.10
    applied = ps["provenance"]["applied_snapshot"]
    assert applied["snapshot_id"] == f"rs_{counts['risk_snapshots']}"
    assert applied["prior_predicted_p"] == pytest.approx(0.74)
    assert applied["new_value"] == 0.10
    assert applied["winning_fact_id"].startswith("lrf_")

    # the assess path applied a learned override but wrote NO learning rows (only the seeded snapshot)
    assert counts["risk_snapshots"] == 1 and counts["prediction_errors"] == 0


def test_low_sample_active_fact_does_not_override(tmp_path) -> None:
    request = candidate_parser.parse(json.loads(FIXTURE.read_text(encoding="utf-8")))
    ctx = composition.resolve_repo_and_db(str(tmp_path), str(tmp_path / "p.db"))
    _seed_active_p_success_fact(ctx.store, ctx.repo.repo_id, value=0.10, sample_size=5)  # below min
    try:
        outcome = assess_controller.assess(
            request, thresholds=request.thresholds, start_path=str(tmp_path),
            **composition.build_assess_ports(request, ctx),
        )
    finally:
        ctx.store.close()
    assert round(outcome.recommended_result.scores["rau"], 2) == 0.31  # gate held -> unchanged
