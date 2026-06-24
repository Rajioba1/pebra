"""ConfidenceGate (Architecture §5/§8.1) — pure, stdlib only.

Receives edit-confidence and band thresholds and returns the confidence band plus any required
evidence action. It does NOT make the final decision — ``decision_engine`` is the sole gate
authority; this just classifies the band the decision engine consumes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceResult:
    band: str  # low | medium | high
    requires_evidence: bool
    edit_confidence: float


def evaluate(edit_confidence: float, thresholds: dict[str, float]) -> ConfidenceResult:
    high = thresholds.get("high_edit_confidence", 0.75)
    low = thresholds.get("low_edit_confidence", 0.50)
    if edit_confidence >= high:
        return ConfidenceResult(band="high", requires_evidence=False, edit_confidence=edit_confidence)
    if edit_confidence >= low:
        return ConfidenceResult(
            band="medium", requires_evidence=False, edit_confidence=edit_confidence
        )
    return ConfidenceResult(band="low", requires_evidence=True, edit_confidence=edit_confidence)
