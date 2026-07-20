"""Pure candidate-review eligibility shared by decision and presentation surfaces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
from typing import Final

from pebra.core.constants import Decision


SANCTION_CONVERTIBLE_GATES: Final[frozenset[int]] = frozenset({2, 3, 4, 9})
REJECT_OVERRIDE_GATES: Final[frozenset[int]] = SANCTION_CONVERTIBLE_GATES - {2}
_REJECT_GATE_NAMES: Final[Mapping[int, str]] = {
    3: "expected_loss_over_threshold",
    4: "negative_rau",
    9: "revision_has_no_credible_benefit",
}


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
    return normalized is Decision.REJECT and reject_override_gate(gates_fired) is not None


def reject_override_gate(gates_fired: Iterable[object]) -> Mapping[object, object] | None:
    """Return validated canonical reject gate evidence, otherwise fail closed."""
    try:
        records = tuple(gates_fired)
    except TypeError:
        return None
    controlling: Mapping[object, object] | None = None
    for record in records:
        if not isinstance(record, Mapping):
            return None
        gate = record.get("gate")
        name = record.get("name")
        advisory = record.get("advisory", False)
        if (
            type(gate) is not int
            or gate <= 0
            or not isinstance(name, str)
            or not name
            or not isinstance(advisory, bool)
        ):
            return None
        if controlling is None and not advisory:
            controlling = record
    if controlling is None:
        return None
    gate = int(controlling["gate"])
    if gate not in REJECT_OVERRIDE_GATES or controlling.get("name") != _REJECT_GATE_NAMES[gate]:
        return None
    if gate == 3:
        values = (controlling.get("expected_loss"), controlling.get("threshold"))
    elif gate == 4:
        values = (controlling.get("rau"),)
    else:
        values = (controlling.get("benefit"),)
    if not all(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        for value in values
    ):
        return None
    return controlling
