"""Pure candidate-review eligibility shared by decision and presentation surfaces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from pebra.core.constants import Decision


SANCTION_CONVERTIBLE_GATES: Final[frozenset[int]] = frozenset({2, 3, 4, 9})


def controlling_gate(gates_fired: Iterable[object]) -> int | None:
    """Return the first non-advisory, structurally valid gate that drove the decision."""
    for entry in gates_fired:
        if not isinstance(entry, Mapping) or entry.get("advisory") is True:
            continue
        gate = entry.get("gate")
        if type(gate) is int and gate > 0:
            return gate
    return None


def reject_override_eligible(decision: Decision | str, gates_fired: Iterable[object]) -> bool:
    """True only for a reject emitted by a sanction-convertible persisted risk gate."""
    try:
        normalized = Decision(decision)
    except (TypeError, ValueError):
        return False
    return (
        normalized is Decision.REJECT
        and controlling_gate(gates_fired) in SANCTION_CONVERTIBLE_GATES
    )


def review_gate_evidence(content: Mapping[str, Any]) -> tuple[int | None, bool]:
    """Extract trusted review disposition from one persisted assessment content object."""
    gates = content.get("gates_fired")
    if not isinstance(gates, list):
        return None, False
    decision = content.get("recommended_decision") or content.get("decision")
    gate = controlling_gate(gates)
    return gate, reject_override_eligible(decision, gates)
