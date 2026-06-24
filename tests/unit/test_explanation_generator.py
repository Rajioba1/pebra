"""Architecture §4/§8 — explanation_generator: pure semantic fields for the human card.

Produces bands + Why lines from the result; the surface composes layout. "RAU" is never a band
value; Value After Risk is a band (Negative/Borderline/Positive/Strong).
"""

from __future__ import annotations

from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core import explanation_generator as eg
from tests.unit.test_assessment_builder import _worked_example_input


def _worked_result():
    return de.decide(ab.build_assessment(_worked_example_input()))


def test_band_helpers() -> None:
    assert eg.risk_level_band(0.10) == "Low"
    assert eg.risk_level_band(0.50) == "Moderate"
    assert eg.risk_level_band(0.80) == "High"
    assert eg.risk_level_band(1.2) == "Critical"
    bands = {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40}
    assert eg.value_after_risk_band(-0.1, bands) == "Negative"
    assert eg.value_after_risk_band(0.10, bands) == "Borderline"
    assert eg.value_after_risk_band(0.31, bands) == "Positive"
    assert eg.value_after_risk_band(0.50, bands) == "Strong"


def test_worked_example_card_fields() -> None:
    ex = eg.render(_worked_result())
    assert ex.risk_level_band == "Moderate"
    assert ex.value_after_risk_band == "Positive"
    assert ex.confidence_band == "high"
    assert ex.confidence_percent == 83
    assert ex.code_sensitivity_label == "High"
    assert ex.expected_damage == 0.10
    assert ex.risk_budget_percent == 50


def test_worked_example_why_lines_are_grounded_in_numbers() -> None:
    ex = eg.render(_worked_result())
    why = " ".join(ex.why)
    assert "50%" in why  # risk budget
    assert "0.10" in why or "0.1" in why  # expected loss
    assert "C3" in why  # criticality
    assert any("Value After Risk is Positive" in line for line in ex.why)
    # never leak the raw "RAU" acronym into human text
    assert all("RAU" not in line for line in ex.why)
