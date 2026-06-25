"""Architecture §8, AD-23 — model_guidance: pure rendering of the pre-edit autonomy envelope.

The packet is NOT a second reasoning system: every field is reconstructable from the result +
action + explanation. Binding fields are what pebra_verify enforces; advisory fields steer only.
"""

from __future__ import annotations

from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core import explanation_generator as eg
from pebra.core import model_guidance as mg
from tests.unit.test_assessment_builder import _worked_example_input


def _packet():
    inp = _worked_example_input()
    result = de.decide(ab.build_assessment(inp))
    explanation = eg.render(result)
    return mg.render(result, inp.action, explanation)


def test_packet_carries_decision_and_risk_mode() -> None:
    p = _packet()
    assert p["decision"] == "proceed"
    assert p["risk_mode"] == "sensitive_context"
    assert "guidance_packet_id" in p


def test_binding_safe_scope_comes_from_candidate_action_envelope() -> None:
    p = _packet()
    files = p["binding"]["safe_scope"]["files"]
    assert "src/auth.py::validate_login" in files
    assert p["binding"]["safe_scope"]["edit_policy"]


def test_risky_scope_items_are_reassessment_by_default() -> None:
    p = _packet()
    actions = {item["action"] for item in p["binding"]["risky_scope"]}
    assert actions == {"requires_reassessment"}


def test_risky_scope_entries_carry_a_signal_for_verify_matching() -> None:
    # pebra_verify maps each risky_scope entry to an actual-diff signal; every entry must name one.
    p = _packet()
    signals = {item.get("signal") for item in p["binding"]["risky_scope"]}
    assert signals == {"contract_change", "dependency_changed", "schema_changed"}


def test_advisory_why_is_reused_from_explanation() -> None:
    p = _packet()
    assert p["advisory"]["why"]
    assert p["provenance"]["why"] == "explanation_generator"


def test_requires_dry_run_false_for_non_dependency_action() -> None:
    p = _packet()
    assert p["binding"]["requires_dry_run"] is False


def test_requires_dry_run_true_for_dependency_change_action() -> None:
    from pebra.core import assessment_builder as ab
    from pebra.core import decision_engine as de
    from pebra.core import explanation_generator as eg
    from pebra.core import model_guidance as mg
    from tests.unit.test_assessment_builder import _worked_example_input

    inp = _worked_example_input()
    inp.action.is_dependency_change = True
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))
    assert packet["binding"]["requires_dry_run"] is True


def test_no_high_risk_triggers_for_worked_example() -> None:
    p = _packet()
    assert p["advisory"]["high_risk_triggers"] == []
    assert p["binding"]["required_controls"] == []
