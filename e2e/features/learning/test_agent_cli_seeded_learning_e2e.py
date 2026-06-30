"""Phase E3 headline — `agent_cli_seeded_learning_e2e`.

The REAL learning loop over the CLI boundary: a scripted agent makes the same risky edit repeatedly and
it keeps going badly (actual_success=False) through the real assess -> verify -> record(completed) ->
learn path; promotion then writes an active learned fact; a second pre-edit assess on a distinct similar
request decides more cautiously because the learned fact overrides p_success downward.

SCOPE: this is the first agent-boundary LEARNING e2e. It is NOT full Tauri-level coverage — the codegraph
graph feature and the dashboard-visual feature are separate. The "seeded history" is labeled as such; it
goes through the real CLI path (no internal seeding, no lowered gate).
"""

from __future__ import annotations

def test_seeded_learning_shifts_the_decision(seeded_learning_state):
    # 1. cold-start baseline (clean repo) — proceed for this fixture.
    baseline = seeded_learning_state.baseline
    assert baseline["recommended_decision"] == "proceed"
    baseline_rau = baseline["scores"]["rau"]

    # 2. seeded history: each sample runs the real pre-edit CLI lifecycle
    # assess -> apply -> verify -> record(completed) -> learn, then resets to clean.
    # 3. promote through the REAL controller (no internal seeding, no lowered gate).
    promo = seeded_learning_state.promotion
    assert promo["risk"]["promoted"] is True, promo["risk"]["veto_reasons"]
    assert promo["risk"]["snapshot_id"]

    scorecard = seeded_learning_state.scorecard
    assert scorecard["shadow_counts"]["risk_snapshots"] >= 1

    # 4. future pre-edit assess: a DISTINCT but scoring-equivalent proposal (request_second_edit),
    # clean tree, now with the active learned snapshot. The cross-request comparison is valid only
    # because the two requests are scoring-identical (differ only in task/id/label) — guarded by
    # test_fixture_equivalence. The proof is necessarily indirect at the CLI boundary:
    # applied_snapshot_provenance is internal (not in assess --json), so learning is evidenced by
    # promotion firing + risk_snapshots>=1 (above) + this RAU drop / decision shift.
    learned = seeded_learning_state.learned
    assert learned["scores"]["rau"] < baseline_rau, (learned["scores"]["rau"], baseline_rau)
    assert learned["recommended_decision"] != baseline["recommended_decision"]
