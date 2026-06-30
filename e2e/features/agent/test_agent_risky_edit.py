"""Phase E3: one full agent cycle over the CLI boundary — assess -> edit -> verify -> record -> learn.

Proves a scripted agent can use PEBRA on real code (get the decision + math, act within the approved
envelope, record the outcome, trigger learning) WITHOUT importing pebra internals. The boundary is
enforced by test_boundary_discipline.
"""

from __future__ import annotations

from e2e.utils import agent_harness as ah

_DECISIONS = {"proceed", "inspect_first", "test_first", "ask_human", "reject"}


def test_agent_completes_full_cycle_over_cli_boundary(risky_repo, e2e_db, request_json_path):
    transcript = ah.run_pre_edit_cycle(risky_repo, e2e_db, request_json_path, actual_success=True)

    payload = transcript.payload
    assert payload["recommended_decision"] in _DECISIONS
    assert payload["recommended_decision"] == "proceed"  # cold-start baseline for this fixture
    assert transcript.verify_passed is True  # verify PROCEEDed within the approved envelope
    # PEBRA returned real math + guidance to the agent:
    assert 0.0 <= payload["scores"]["rau"] <= 1.0
    assert isinstance(
        payload["model_guidance_packet"]["binding"]["required_checks_before_commit"], list
    )
    # learning measured the prediction (observed or censored):
    assert transcript.learn_result["observed"] + transcript.learn_result["censored"] >= 1
