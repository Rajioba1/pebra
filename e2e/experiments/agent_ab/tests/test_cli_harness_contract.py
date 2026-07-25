"""Pins the experiment's gate-envelope consumer to production schema-2 vocabulary."""

from __future__ import annotations

import pytest

from e2e.utils import cli_harness


@pytest.mark.parametrize(
    ("permission", "tier"),
    (
        ("ask", "consulted_review"),
        ("deny", "consulted_review_unavailable"),
    ),
)
def test_experiment_accepts_confirmation_required_proceed_gate_pairs(permission, tier):
    payload = {
        "schema_version": 2,
        "permission": permission,
        "tier": tier,
        "reason": "confirmation required before mutation",
        "warn": None,
        "matched_assessment_id": "asm_1",
        "risk_summary": {
            "decision": "proceed",
            "expected_loss": 0.2,
            "benefit": 0.5,
            "rau": 0.3,
        },
    }

    assert cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"]) == payload
