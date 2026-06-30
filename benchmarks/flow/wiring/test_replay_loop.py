"""Learning-loop replay benchmark — prove the synthetic corpus drives the real learning cycle.

This is a DETERMINISTIC WIRING PROOF, NOT an agent/product e2e: the cycle starts from authored
prediction rows, not from ``assess_controller.assess()`` evidence gathering, and there is no agent, no
real repo, and no dashboard. The true agent/product e2e (agent edits real code → CLI/MCP → learn →
reassess → dashboard) lives at the repo-root ``e2e/`` suite.
"""

from __future__ import annotations

from benchmarks.flow import compare


def test_replay_loop_promotes_and_applies_snapshot(tmp_path):
    artifact = compare.run_and_compare(tmp_path)

    assert artifact["passed"] is True, artifact["failure_reasons"]
    assert artifact["failure_reasons"] == []
    assert artifact["promotion_fired"] is True
    assert artifact["chain_valid_genesis"] is True
    assert artifact["chain_valid_learned"] is True
    assert artifact["genesis_loaded_snapshot"] is False
    assert artifact["learned_loaded_snapshot"] is True
    assert artifact["deterministic"] is True
    assert artifact["learned_brier"] < artifact["genesis_brier"]
    assert artifact["calibration_quality"]["claim_scope"] == "synthetic_fixture_only"
    assert artifact["calibration_quality"]["verdict"] == "improved_on_fixture"
    assert "measure_learning -> run_promotion" in artifact["cycle_note"]
