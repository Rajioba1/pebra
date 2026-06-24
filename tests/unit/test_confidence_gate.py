"""Architecture §5/§8.1 — ConfidenceGate: edit_confidence -> band + required evidence action.

It does NOT make the final decision (decision_engine is the sole gate authority); it returns the
band the decision engine consumes.
"""

from __future__ import annotations

from pebra.core import confidence_gate as cg

THRESHOLDS = {"high_edit_confidence": 0.75, "low_edit_confidence": 0.50}


def test_high_band_when_at_or_above_high_threshold() -> None:
    result = cg.evaluate(0.83, THRESHOLDS)
    assert result.band == "high"
    assert result.requires_evidence is False


def test_medium_band_between_thresholds() -> None:
    result = cg.evaluate(0.60, THRESHOLDS)
    assert result.band == "medium"


def test_low_band_below_low_threshold_requires_evidence() -> None:
    result = cg.evaluate(0.40, THRESHOLDS)
    assert result.band == "low"
    assert result.requires_evidence is True
