"""Architecture §8, AD-23 — model_guidance: pure rendering of the pre-edit autonomy envelope.

The packet is NOT a second reasoning system: every field is reconstructable from the result +
action + explanation. Binding fields are what pebra_verify enforces; advisory fields steer only.
"""

from __future__ import annotations

from dataclasses import replace

from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core import explanation_generator as eg
from pebra.core import model_guidance as mg
from pebra.core import models as m
from pebra.core.constants import Decision
from tests.unit.test_assessment_builder import _worked_example_input


def _packet():
    inp = _worked_example_input()
    result = de.decide(ab.build_assessment(inp))
    explanation = eg.render(result)
    return mg.render(result, inp.action, explanation)


def test_codegraph_structural_tier_adds_honesty_note_on_proceed() -> None:
    inp = _worked_example_input()
    # A proceed classified from the coarse multi-language tier must be flagged as not signature-verified.
    inp = replace(inp, symbol_diff_evidence=replace(
        inp.symbol_diff_evidence, structure_tier="codegraph_structural"))
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))
    assert result.recommended_decision.value == "proceed"
    assert any("CodeGraph structure" in s for s in packet["advisory"]["suggested_inspection"])


def test_codegraph_semantic_tier_also_adds_honesty_note_on_proceed() -> None:
    # The semantic tier proves ONE owner's signature, not a whole-file public-surface guarantee -> it
    # keeps the same honesty note on proceed. Pins the semantic branch of model_guidance's set literal.
    inp = _worked_example_input()
    inp = replace(inp, symbol_diff_evidence=replace(
        inp.symbol_diff_evidence, structure_tier="codegraph_semantic"))
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))
    assert result.recommended_decision.value == "proceed"
    assert any("CodeGraph structure" in s for s in packet["advisory"]["suggested_inspection"])


def test_default_unavailable_tier_adds_no_note() -> None:
    # The long-standing default tier must NOT add noise to every proceed.
    assert not any(
        "CodeGraph structure" in s for s in _packet()["advisory"]["suggested_inspection"]
    )


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
    inp = _worked_example_input()
    inp.action.is_dependency_change = True
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))
    assert packet["binding"]["requires_dry_run"] is True


def test_no_high_risk_triggers_for_worked_example() -> None:
    p = _packet()
    assert p["advisory"]["high_risk_triggers"] == []
    assert p["binding"]["required_controls"] == []


def test_revise_safer_guidance_is_structural_not_generic() -> None:
    inp = replace(
        _worked_example_input(),
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::PublicContract", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))
    safer_route = packet["advisory"]["safer_route"]

    assert packet["decision"] == "revise_safer"
    assert packet["advisory"]["safer_alternative"] == safer_route["summary"]
    assert "resubmit" in safer_route["summary"].lower()
    assert any("public" in c.lower() for c in safer_route["constraints"])
    rendered_constraints = " ".join(safer_route["constraints"]).lower()
    assert "alias" in rendered_constraints
    assert "wrapper" in rendered_constraints
    assert "compatibility" in rendered_constraints
    assert "lower-impact owner or file" in rendered_constraints
    assert "declare every intended file" in rendered_constraints
    assert "inside the assessed file scope" not in rendered_constraints
    assert packet["binding"]["safe_scope"]["files"] == mg._safe_scope_files(inp.action)
    assert packet["provenance"]["safer_route"] == "decision + symbol scope evidence + candidate envelope"


def test_revise_safer_guidance_stays_domain_agnostic_for_gamma_paths() -> None:
    inp = _worked_example_input()
    action = replace(
        inp.action,
        expected_files=["src/Numerics/SpecialFunctions/Gamma.cs"],
        proposed_patch=(
            "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs "
            "b/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "--- a/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "+++ b/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "@@ -86,7 +86,7 @@\n"
            "-                    s += GammaDk[i]/(i - z);\n"
            "+                    s += LanczosSum(z, i);\n"
        ),
    )
    inp = replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=[],
            max_change_kind="UNKNOWN",
            consequential_symbol_changed=True,
        ),
        fanin_evidence=m.FanInEvidence(
            resolution_method="location",
            graph_freshness="fresh",
            resolved_symbol_count=3,
        ),
    )

    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, action, eg.render(result))
    safer_route = packet["advisory"]["safer_route"]

    assert result.recommended_decision is Decision.REVISE_SAFER
    rendered = str(safer_route).lower()
    assert "candidate_verification" not in safer_route
    assert "gammaln" not in rendered
    assert "gammadk" not in rendered
    assert "lanczos" not in rendered
    assert "denominator" not in rendered


def test_repo_blast_fraction_reaches_advisory_risk_facts_when_trusted() -> None:
    inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            resolution_method="location",
            graph_freshness="fresh",
            modify_transitive_impact_count=28,
            modify_repo_blast_fraction=0.28,
            modify_repo_graph_node_count=100,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    packet = mg.render(result, inp.action, eg.render(result))

    assert packet["advisory"]["risk_facts"]["repo_blast_fraction"] == 0.28
    assert packet["advisory"]["risk_facts"]["repo_blast_percent"] == 28.0
    assert packet["advisory"]["risk_facts"]["repo_blast_node_count"] == 28
    assert packet["advisory"]["risk_facts"]["repo_graph_node_count"] == 100


def test_repo_blast_fraction_absent_from_guidance_when_graph_untrusted_or_absent() -> None:
    absent = de.decide(ab.build_assessment(_worked_example_input()))
    absent_packet = mg.render(absent, _worked_example_input().action, eg.render(absent))

    untrusted_inp = replace(
        _worked_example_input(),
        fanin_evidence=m.FanInEvidence(
            resolution_method="unresolved",
            graph_freshness="stale",
            modify_transitive_impact_count=28,
            modify_repo_blast_fraction=0.28,
            modify_repo_graph_node_count=100,
        ),
    )
    untrusted = de.decide(ab.build_assessment(untrusted_inp))
    untrusted_packet = mg.render(untrusted, untrusted_inp.action, eg.render(untrusted))

    for packet in (absent_packet, untrusted_packet):
        facts = packet["advisory"]["risk_facts"]
        assert "repo_blast_fraction" not in facts
        assert "repo_blast_percent" not in facts
        assert "repo_blast_node_count" not in facts
        assert "repo_graph_node_count" not in facts
