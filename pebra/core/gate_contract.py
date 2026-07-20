"""Stable vocabulary and validation matrices for pre-edit gate decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from types import MappingProxyType
from typing import Final, Mapping

from pebra.core.constants import Decision


class GatePermission(StrEnum):
    CONTINUE = "allow"
    RETURN_CANDIDATE = "deny"
    REQUEST_HUMAN = "ask"


class GateTier(StrEnum):
    PASS = "pass"
    FAIL_OPEN = "fail_open"
    MUST_CONSULT = "must_consult"
    CANDIDATE_UNVERIFIABLE = "candidate_unverifiable"
    CANDIDATE_UNBOUND = "candidate_unbound"
    CANDIDATE_MISMATCH = "candidate_mismatch"
    CANDIDATE_INCOMPLETE = "candidate_incomplete"
    CONSULTED = "consulted"
    CONSULTED_REVISE = "consulted_revise"
    CONSULTED_PREREQUISITE = "consulted_prerequisite"
    CONSULTED_REVIEW = "consulted_review"
    CONSULTED_REJECT_REVIEW = "consulted_reject_review"
    CONSULTED_REVIEW_UNAVAILABLE = "consulted_review_unavailable"


@dataclass(frozen=True)
class GateRiskSummary:
    decision: Decision | str
    expected_loss: float
    benefit: float
    rau: float

    def __post_init__(self) -> None:
        decision = Decision(self.decision)
        numeric = (self.expected_loss, self.benefit, self.rau)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in numeric
        ):
            raise ValueError("gate risk summary values must be finite numbers")
        try:
            normalized = tuple(float(value) for value in numeric)
        except OverflowError as exc:
            raise ValueError("gate risk summary values must be finite numbers") from exc
        if any(not math.isfinite(value) for value in normalized):
            raise ValueError("gate risk summary values must be finite numbers")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "expected_loss", normalized[0])
        object.__setattr__(self, "benefit", normalized[1])
        object.__setattr__(self, "rau", normalized[2])

    def as_dict(self) -> dict[str, str | float]:
        return {
            "decision": self.decision.value,
            "expected_loss": float(self.expected_loss),
            "benefit": float(self.benefit),
            "rau": float(self.rau),
        }


GATE_SCHEMA_VERSION: Final[int] = 2
_ASSESSMENT_ID_RE: Final[re.Pattern[str]] = re.compile(r"asm_[1-9][0-9]*")
ALLOWED_PERMISSION_TIERS: Final[Mapping[GatePermission, frozenset[GateTier]]] = (
    MappingProxyType({
        GatePermission.CONTINUE: frozenset({
            GateTier.PASS,
            GateTier.FAIL_OPEN,
            GateTier.CONSULTED,
        }),
        GatePermission.REQUEST_HUMAN: frozenset({
            GateTier.CONSULTED_REVIEW,
            GateTier.CONSULTED_REJECT_REVIEW,
        }),
        GatePermission.RETURN_CANDIDATE: frozenset(set(GateTier) - {
            GateTier.PASS,
            GateTier.FAIL_OPEN,
            GateTier.CONSULTED,
            GateTier.CONSULTED_REJECT_REVIEW,
        }),
    })
)
ALLOWED_RISK_DECISIONS: Final[
    Mapping[tuple[GatePermission, GateTier], frozenset[Decision]]
] = MappingProxyType({
    (GatePermission.CONTINUE, GateTier.CONSULTED): frozenset({Decision.PROCEED}),
    (GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_REVISE): frozenset({
        Decision.REVISE_SAFER,
    }),
    (GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_PREREQUISITE): frozenset({
        Decision.INSPECT_FIRST,
        Decision.TEST_FIRST,
    }),
    (GatePermission.REQUEST_HUMAN, GateTier.CONSULTED_REVIEW): frozenset({
        Decision.ASK_HUMAN,
    }),
    (GatePermission.REQUEST_HUMAN, GateTier.CONSULTED_REJECT_REVIEW): frozenset({
        Decision.REJECT,
    }),
    (GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_REVIEW): frozenset({
        Decision.REJECT,
    }),
    (GatePermission.RETURN_CANDIDATE, GateTier.CONSULTED_REVIEW_UNAVAILABLE): frozenset({
        Decision.ASK_HUMAN,
    }),
})
