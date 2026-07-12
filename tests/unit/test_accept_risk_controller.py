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


_BINDING = {
    "algorithm": "sha256-normalized-content-v1",
    "files": {"src/a.py": "a" * 64},
}


def _spec(**overrides):
    spec = {
        "assessment_id": "asm_1",
        "action_id": "a1",
        "risk_profile": {
            "assessment_id": "asm_1",
            "action_id": "a1",
            "candidate_binding": _BINDING,
        },
    }
    spec.update(overrides)
    return spec


def test_accept_risk_creates_profile_bound_sanction() -> None:
    port = FakeSanctionPort()
    sid = arc.accept_risk(
        "repo_x",
        {
            **_spec(),
            "pre_edit_authorization_controls_satisfied": True,
            "converts_gates": [2, 3, 4],
            "high_risk_triggers": [{"risk_class": "payment_side_effect"}],
        },
        sanction_port=port,
    )
    assert sid == "sx_1"
    _, sanction = port.created[0]
    assert sanction["valid"] is True
    assert sanction["risk_profile"]["candidate_binding"] == _BINDING
    assert sanction["converts_gates"] == [2, 3, 4]


def test_accept_risk_requires_a_risk_profile() -> None:
    with pytest.raises(ValueError):
        arc.accept_risk("repo_x", {}, sanction_port=FakeSanctionPort())


def test_accept_risk_default_authorizes_revision_escalation_gate() -> None:
    port = FakeSanctionPort()

    arc.accept_risk(
        "repo_x",
        _spec(),
        sanction_port=port,
    )

    assert port.created[0][1]["converts_gates"] == [2, 3, 4, 9]


@pytest.mark.parametrize(
    "spec",
    [
        _spec(action_id="a2"),
        _spec(assessment_id="asm_2"),
        _spec(risk_profile={"assessment_id": "asm_1", "action_id": "a1"}),
        _spec(risk_profile={
            "assessment_id": "asm_1",
            "action_id": "a1",
            "candidate_binding": {"algorithm": "bad", "files": {"src/a.py": "abc"}},
        }),
    ],
)
def test_accept_risk_rejects_unbound_or_mismatched_candidate(spec) -> None:
    with pytest.raises(ValueError):
        arc.accept_risk("repo_x", spec, sanction_port=FakeSanctionPort())
