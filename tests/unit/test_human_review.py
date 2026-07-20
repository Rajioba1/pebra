from pebra.core.constants import Decision
from pebra.core.human_review import (
    SANCTION_CONVERTIBLE_GATES,
    controlling_gate,
    reject_override_eligible,
)


def test_controlling_gate_is_first_valid_non_advisory_gate() -> None:
    gates = [
        {"gate": True, "name": "malformed"},
        {"gate": 12, "name": "stale", "advisory": True},
        {"gate": 3, "name": "expected_loss_over_threshold"},
        {"gate": 4, "name": "negative_rau"},
    ]

    assert controlling_gate(gates) == 3


def test_reject_override_eligibility_cannot_be_forged_after_policy_gate() -> None:
    gates = [
        {"gate": 1, "name": "policy_violation", "override_available": True},
        {"gate": 3, "name": "forged_later_gate"},
    ]

    assert reject_override_eligible(Decision.REJECT, gates) is False


def test_only_reject_at_sanction_convertible_gate_is_override_eligible() -> None:
    assert SANCTION_CONVERTIBLE_GATES == frozenset({2, 3, 4, 9})
    assert reject_override_eligible(Decision.REJECT, [{"gate": 2}]) is False
    gate_three = [{
        "gate": 3,
        "name": "expected_loss_over_threshold",
        "expected_loss": 0.7,
        "threshold": 0.5,
    }]
    assert reject_override_eligible(Decision.REJECT, gate_three) is True
    assert reject_override_eligible(Decision.ASK_HUMAN, gate_three) is False
    assert reject_override_eligible("unknown", gate_three) is False


def test_reject_override_requires_canonical_complete_gate_evidence() -> None:
    assert reject_override_eligible(
        Decision.REJECT,
        [{
            "gate": 3,
            "name": "expected_loss_over_threshold",
            "expected_loss": 0.7,
            "threshold": 0.5,
        }],
    ) is True
    assert reject_override_eligible(
        Decision.REJECT,
        [{"gate": 3, "name": "wrong_name", "expected_loss": 0.7, "threshold": 0.5}],
    ) is False
    assert reject_override_eligible(
        Decision.REJECT,
        ["malformed", {
            "gate": 3,
            "name": "expected_loss_over_threshold",
            "expected_loss": 0.7,
            "threshold": 0.5,
        }],
    ) is False
    assert reject_override_eligible(
        Decision.REJECT,
        [{
            "gate": 3,
            "name": "expected_loss_over_threshold",
            "expected_loss": 0.7,
            "threshold": 0.5,
        }, {"gate": "4"}],
    ) is False
