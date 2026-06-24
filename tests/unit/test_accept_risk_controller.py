"""Architecture AD-26 — accept_risk_controller: creates a profile-bound sanction event."""

from __future__ import annotations

import pytest

from pebra.app import accept_risk_controller as arc


class FakeSanctionPort:
    def __init__(self):
        self.created = []

    def active_sanction(self, repo_id, action):
        return None

    def create_sanction(self, repo_id, sanction):
        self.created.append((repo_id, sanction))
        return f"sx_{len(self.created)}"


def test_accept_risk_creates_profile_bound_sanction() -> None:
    port = FakeSanctionPort()
    sid = arc.accept_risk(
        "repo_x",
        {
            "risk_profile": "rp_abc",
            "pre_edit_authorization_controls_satisfied": True,
            "converts_gates": [2, 3, 4],
            "high_risk_triggers": [{"risk_class": "payment_side_effect"}],
        },
        sanction_port=port,
    )
    assert sid == "sx_1"
    _, sanction = port.created[0]
    assert sanction["valid"] is True
    assert sanction["risk_profile"] == "rp_abc"
    assert sanction["converts_gates"] == [2, 3, 4]


def test_accept_risk_requires_a_risk_profile() -> None:
    with pytest.raises(ValueError):
        arc.accept_risk("repo_x", {}, sanction_port=FakeSanctionPort())
