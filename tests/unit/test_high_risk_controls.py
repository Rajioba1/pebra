"""Architecture §5/§8, AD-26 — high_risk_controls: pure trigger -> control-blueprint selector."""

from __future__ import annotations

from pebra.core import high_risk_controls as hrc


def test_no_triggers_means_no_controls() -> None:
    selection = hrc.select_controls([])
    assert selection.required_controls == []
    assert selection.control_blueprint_ids == []


def test_payment_side_effect_selects_payment_blueprint() -> None:
    selection = hrc.select_controls([{"risk_class": "payment_side_effect"}])
    assert "payment_change" in selection.control_blueprint_ids
    assert "sandbox_payment_tests" in selection.required_controls
    assert "idempotency_evidence" in selection.required_controls


def test_unknown_risk_class_gets_conservative_default_control() -> None:
    selection = hrc.select_controls([{"risk_class": "novel_thing"}])
    assert selection.required_controls  # never empty for a real trigger
