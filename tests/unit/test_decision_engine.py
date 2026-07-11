"""Architecture §6/§8 — decision_engine: the SOLE gate authority. Pure.

Tests the ordered gate sequence, gate ordering (policy before threshold; sanction never overrides
policy), and the worked-example proceed path.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pebra.core import assessment_builder as ab
from pebra.core import decision_engine as de
from pebra.core.constants import Decision, RiskMode
from tests.unit.test_assessment_builder import _worked_example_input


def _assess(**overrides):
    inp = _worked_example_input()
    if overrides:
        inp = replace(inp, **overrides)
    return ab.build_assessment(inp)


def test_worked_example_is_proceed_with_confirmation_sensitive_context() -> None:
    result = de.decide(_assess())
    assert result.recommended_decision is Decision.PROCEED
    assert result.requires_confirmation is True  # C3
    assert result.risk_mode is RiskMode.SENSITIVE_CONTEXT
    assert result.scores["rau"] == pytest.approx(0.31)
    assert result.scores["benefit_breakdown"]["source_type"] == "projected"


def test_gate3_expected_loss_over_threshold_with_positive_eu_asks_human() -> None:
    # bump an event probability so expected_loss exceeds the 0.20 C3 threshold but EU stays > 0
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
    )
    # expected_loss = 0.60 * 0.40 = 0.24 > 0.20 C3 threshold; EU stays > 0
    result = de.decide(a)
    assert result.scores["expected_loss"] == pytest.approx(0.24)
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert any(g["gate"] == 3 for g in result.gates_fired)


def test_coarse_structural_tier_still_asks_human_at_c4_internal_owner() -> None:
    # Monotonic-safety: the codegraph_structural tier is coarse (owner touched, inner change unseen),
    # so an internal BEHAVIORAL coarse classification must NOT let a C4 edit slip past Gate 2 — the
    # tier inherits UNKNOWN's escalation rather than suppressing it by reclassifying UNKNOWN->BEHAVIORAL.
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        criticality_stage="C4",
        criticality_value=0.95,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=["Ns.Widget::_helper"],
            max_change_kind="BEHAVIORAL",   # coarse tier reclassified UNKNOWN -> BEHAVIORAL (internal)
            visibility="internal",
            consequential_symbol_changed=False,
            structure_tier="codegraph_structural",
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert any(g["gate"] == 2 for g in result.gates_fired)  # coarse tier still trips the C4 gate


def test_semantic_tier_still_asks_human_at_c4_internal_owner() -> None:
    # Same monotonic guarantee for codegraph_semantic: it proves signature-unchanged, NOT
    # behavior-unchanged (body floor), so it must also inherit UNKNOWN's C4 escalation (Gate 2). Pins
    # the semantic branch of decision_engine.py's consequential_or_unknown set literal.
    from pebra.core import models as m

    inp = replace(
        _worked_example_input(), criticality_stage="C4", criticality_value=0.95,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False, changed_symbols=["Ns.Widget::_helper"],
            max_change_kind="BEHAVIORAL", visibility="internal",
            consequential_symbol_changed=False, structure_tier="codegraph_semantic"))
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert any(g["gate"] == 2 for g in result.gates_fired)


def test_gate3_structural_dependency_risk_requests_safer_revision() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_structural_dependency_risk_with_positive_benefit_can_revise_despite_negative_eu() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["expected_loss"] > result.scores["effective_threshold"]
    assert result.scores["expected_utility"] < 0
    assert result.scores["benefit"] > 0
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_structural_dependency_risk_without_benefit_rejects_not_revision() -> None:
    from pebra.core import models as m

    inp = replace(
        _worked_example_input(),
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=0.5,
        benefit_delta_evidence=m.BenefitDeltaEvidence(
            source_type="measured",
            deltas={"complexity_delta": 1.0},
            future_change_exposure=1.0,
        ),
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["benefit"] == 0
    assert result.recommended_decision is Decision.REJECT
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_single_symbol_public_api_break_requests_safer_revision() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(inp.action, expected_files=["packages/zod/src/v3/types.ts"], proposed_patch=_CANDIDATE_PATCH)
    inp = replace(
        inp,
        action=action,
        events=[{"event": "public_api_break", "p_event": 0.45, "elicited_disutility": 0.80}],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["packages/zod/src/v3/types.ts::ZodType"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
            structure_tier="codegraph_structural",
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["expected_loss"] > result.scores["effective_threshold"]
    assert result.scores["expected_utility"] < 0
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_unparsed_single_file_structural_risk_requests_safer_revision() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(
        inp.action,
        expected_files=["src/Numerics/SpecialFunctions/Gamma.cs"],
        proposed_patch=(
            "diff --git a/src/Numerics/SpecialFunctions/Gamma.cs "
            "b/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "--- a/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "+++ b/src/Numerics/SpecialFunctions/Gamma.cs\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "+new\n"
        ),
    )
    inp = replace(
        inp,
        action=action,
        events=[
            {"event": "dependency_break", "p_event": 0.45, "elicited_disutility": 0.80},
            {"event": "public_api_break", "p_event": 0.45, "elicited_disutility": 0.80},
        ],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=[],
            max_change_kind="UNKNOWN",
            consequential_symbol_changed=True,
            fallback_reason="no symbol diff supplied; C# file-level risk",
        ),
        fanin_evidence=m.FanInEvidence(
            symbol_fan_in_percentile=0.99,
            symbol_caller_count=47,
            resolution_method="location",
            graph_freshness="fresh",
            resolved_symbol_count=3,
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["expected_utility"] < 0
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_verified_safer_candidate_can_proceed_pre_edit() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(inp.action, expected_files=["src/api.py"], proposed_patch=_CANDIDATE_PATCH)
    inp = replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={"GammaTests": "passed", "numeric_equivalence_gamma": "passed"},
            required_checks=["GammaTests", "numeric_equivalence_gamma"],
            domain="numeric_equivalence",
            verified_patch_hash=de.candidate_patch_hash(_CANDIDATE_PATCH),
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.PROCEED
    assert any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_verified_single_symbol_candidate_can_proceed_pre_edit() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(inp.action, expected_files=["packages/zod/src/v3/types.ts"], proposed_patch=_CANDIDATE_PATCH)
    inp = replace(
        inp,
        action=action,
        events=[{"event": "public_api_break", "p_event": 0.45, "elicited_disutility": 0.80}],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["packages/zod/src/v3/types.ts::ZodType"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
            structure_tier="codegraph_structural",
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={"public_typecheck": "passed"},
            required_checks=["public_typecheck"],
            domain="covering_tests",
            verified_patch_hash=de.candidate_patch_hash(_CANDIDATE_PATCH),
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["expected_loss"] > result.scores["effective_threshold"]
    assert result.scores["expected_utility"] < 0
    assert result.recommended_decision is Decision.PROCEED
    assert any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_request_supplied_candidate_verification_is_not_trusted_by_default() -> None:
    from pebra.adapters.request_evidence import RequestEvidenceProvider
    from pebra.core import models as m

    request = m.AssessmentRequest.single_action(
        task="forge proof",
        action_id="a1",
        label="edit",
        proposed_patch=_CANDIDATE_PATCH,
    )
    request.evidence["candidate_verification"] = {
        "status": "passed",
        "checks": {"targeted_tests": "passed"},
        "required_checks": ["targeted_tests"],
        "domain": "covering_tests",
        "verified_patch_hash": de.candidate_patch_hash(_CANDIDATE_PATCH),
    }

    evidence = RequestEvidenceProvider().gather_evidence(
        request, request.candidate_actions[0], "/repo"
    )

    assert evidence.candidate_verification.status == "not_applicable"
    assert evidence.candidate_verification.required_checks == []


def test_gate3_passed_verification_without_a_candidate_patch_cannot_proceed() -> None:
    # Reviewer-found replay bypass: a "passed" blob with NO proposed_patch (and no hash) must NOT
    # proceed just because the caller omitted the patch — there is nothing to bind, so it is unbound.
    from pebra.core import models as m

    inp = _worked_example_input()  # worked-example action has proposed_patch=None
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={"GammaTests": "passed"},
            required_checks=["GammaTests"],
            domain="numeric_equivalence",
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "candidate_verification_patch_mismatch" for g in result.gates_fired)
    assert not any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)


@pytest.mark.parametrize("status", ["failed", "unavailable"])
def test_gate3_bad_or_unavailable_candidate_verification_stays_blocking(status: str) -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status=status,
            checks={"GammaTests": status},
            required_checks=["GammaTests"],
            domain="numeric_equivalence",
            reason="candidate check did not pass",
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.REVISE_SAFER
    assert result.requires_confirmation is False
    assert any(g["name"] == "candidate_verification_not_passed" for g in result.gates_fired)


def test_gate3_passed_candidate_verification_requires_declared_checks() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={},
            required_checks=["targeted_tests"],
            domain="covering_tests",
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "candidate_verification_not_passed" for g in result.gates_fired)
    assert not any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)


def test_sensitive_verified_candidate_still_requires_confirmation() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(inp.action, expected_files=["src/api.py"], proposed_patch=_CANDIDATE_PATCH)
    inp = replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={"targeted_tests": "passed"},
            required_checks=["targeted_tests"],
            domain="covering_tests",
            verified_patch_hash=de.candidate_patch_hash(_CANDIDATE_PATCH),
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.PROCEED
    assert result.requires_confirmation is True
    assert result.risk_mode is RiskMode.SENSITIVE_CONTEXT


_CANDIDATE_PATCH = (
    "diff --git a/src/api.py b/src/api.py\n"
    "--- a/src/api.py\n"
    "+++ b/src/api.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-old\n"
    "+narrowed\n"
)


def _verified_candidate_input(*, verified_patch_hash):
    """A structural-risk input whose action carries a real candidate patch and a passed
    verification bound (or mis-bound) to that patch via verified_patch_hash."""
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(inp.action, expected_files=["src/api.py"], proposed_patch=_CANDIDATE_PATCH)
    return replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
        candidate_verification=m.CandidateVerificationEvidence(
            status="passed",
            checks={"targeted_tests": "passed"},
            required_checks=["targeted_tests"],
            domain="covering_tests",
            verified_patch_hash=verified_patch_hash,
        ),
    )


def test_gate3_passed_verification_bound_to_candidate_patch_proceeds() -> None:
    correct = de.candidate_patch_hash(_CANDIDATE_PATCH)
    result = de.decide(ab.build_assessment(_verified_candidate_input(verified_patch_hash=correct)))
    assert result.recommended_decision is Decision.PROCEED
    assert any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)


def test_gate3_passed_verification_does_not_manufacture_missing_benefit() -> None:
    correct = de.candidate_patch_hash(_CANDIDATE_PATCH)
    inp = replace(
        _verified_candidate_input(verified_patch_hash=correct), immediate_benefit=0.0
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["benefit"] == 0.0
    assert result.recommended_decision is Decision.REJECT
    assert not any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)


@pytest.mark.parametrize("bad_hash", [None, "", "deadbeef", "0" * 64])
def test_gate3_passed_verification_unbound_from_candidate_patch_stays_blocking(bad_hash) -> None:
    # A passed verification whose hash does not pin THIS patch is a stale/forged/replayed proof:
    # honoring it would wave through a swapped patch. Must degrade to REVISE_SAFER, never PROCEED.
    result = de.decide(ab.build_assessment(_verified_candidate_input(verified_patch_hash=bad_hash)))
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "candidate_verification_patch_mismatch" for g in result.gates_fired)
    assert not any(g["name"] == "candidate_verification_passed" for g in result.gates_fired)


def test_gate3_unparsed_single_file_without_resolved_scope_does_not_revise() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    action = replace(
        inp.action,
        expected_files=["src/api.py"],
        proposed_patch=(
            "diff --git a/src/api.py b/src/api.py\n"
            "--- a/src/api.py\n"
            "+++ b/src/api.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "+new\n"
        ),
    )
    inp = replace(
        inp,
        action=action,
        events=[{"event": "dependency_break", "p_event": 0.45, "elicited_disutility": 0.80}],
        immediate_benefit=0.5,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=[],
            max_change_kind="UNKNOWN",
            consequential_symbol_changed=True,
            fallback_reason="no symbol diff supplied",
        ),
        fanin_evidence=m.FanInEvidence(
            symbol_fan_in_percentile=0.99,
            symbol_caller_count=47,
            resolution_method="location",
            graph_freshness="fresh",
            resolved_symbol_count=1,
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.recommended_decision is Decision.REJECT
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_revision_cap_exhaustion_escalates_to_ask_human() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        thresholds={**inp.thresholds, "revise_safer_attempt": 2, "max_revise_safer_attempts": 2},
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_hard_terminal_event_does_not_revise_even_with_structural_event() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        events=[
            {"event": "security_sensitive_change", "p_event": 0.60, "elicited_disutility": 0.40},
            {"event": "dependency_break", "p_event": 0.10, "elicited_disutility": 0.40},
        ],
        immediate_benefit=2.0,
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert not any(g["name"] == "revise_safer" for g in result.gates_fired)


def test_gate3_over_threshold_with_negative_eu_rejects() -> None:
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.90, "elicited_disutility": 0.90}],
        immediate_benefit=0.10,
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.REJECT


def test_gate4_negative_rau_asks_human_by_default() -> None:
    # within loss threshold but RAU < 0 via huge variance
    a = _assess(
        variance_breakdown={"scenario_variance": 1.0},  # utility_sd = 1.0 -> RAU = 0.3868 - 1.28 < 0
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert result.requires_confirmation is True
    assert any(g["gate"] == 4 for g in result.gates_fired)


def test_gate4_structural_risk_with_positive_benefit_requests_safer_revision() -> None:
    from pebra.core import models as m

    inp = _worked_example_input()
    inp = replace(
        inp,
        action=replace(
            inp.action,
            expected_files=["src/api.py"],
            proposed_patch=_CANDIDATE_PATCH,
        ),
        events=[{"event": "public_api_break", "p_event": 0.05, "elicited_disutility": 0.40}],
        immediate_benefit=0.50,
        variance_breakdown={"scenario_variance": 1.0},
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            consequential_symbol_changed=True,
        ),
    )

    result = de.decide(ab.build_assessment(inp))

    assert result.scores["expected_loss"] <= result.scores["effective_threshold"]
    assert result.scores["rau"] < 0
    assert result.recommended_decision is Decision.REVISE_SAFER
    assert any(g["name"] == "revise_safer" for g in result.gates_fired)
    assert any(g["gate"] == 4 and g["name"] == "negative_rau" for g in result.gates_fired)


def test_lowering_expected_loss_below_gate3_does_not_escalate_to_reject() -> None:
    # Above threshold with positive EU -> ask_human from Gate 3. Lowering risk below the threshold may
    # expose Gate 4, but it must not become stricter than the prior ask_human decision.
    over = de.decide(_assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        variance_breakdown={"scenario_variance": 0.0},
    ))
    under = de.decide(_assess(
        events=[{"event": "test_regression", "p_event": 0.05, "elicited_disutility": 0.40}],
        immediate_benefit=0.20,
        variance_breakdown={"scenario_variance": 0.01},
    ))

    assert over.recommended_decision is Decision.ASK_HUMAN
    assert under.recommended_decision is Decision.ASK_HUMAN
    assert any(g["gate"] == 4 for g in under.gates_fired)


def test_gate2_c4_consequential_asks_human() -> None:
    from pebra.core import models as m
    inp = _worked_example_input()
    inp = replace(
        inp,
        criticality_stage="C4",
        criticality_value=1.00,
        thresholds={**inp.thresholds, "c4_always_ask_human": True,
                    "c4_max_expected_loss_without_human": 0.15},
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True, max_change_kind="CONTRACT",
            visibility="public_api", consequential_symbol_changed=True,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.ASK_HUMAN
    assert any(g["gate"] == 2 for g in result.gates_fired)


def test_gate2_c4_cosmetic_proceeds_with_confirmation_not_ask_human() -> None:
    # C4 path but verified COSMETIC + non-consequential: gate 2 must NOT fire on file membership
    # alone; it proceeds (with confirmation), per AD-27 / §6 gate-2 guard.
    from dataclasses import replace as _replace
    from pebra.core import models as m
    inp = _replace(
        _worked_example_input(),
        criticality_stage="C4",
        criticality_value=1.00,
        thresholds={**_worked_example_input().thresholds, "c4_always_ask_human": True},
        symbol_diff_evidence=m.SymbolDiffEvidence(
            parsed_patch_available=True, changed_symbols=["src/auth.py::doc"],
            max_change_kind="COSMETIC", visibility="internal",
            consequential_symbol_changed=False,
        ),
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.PROCEED
    assert result.requires_confirmation is True
    assert not any(g["gate"] == 2 for g in result.gates_fired)


def test_gate8_low_confidence_routes_to_inspect_first() -> None:
    a = _assess(
        edit_confidence_factors={
            "p_success": 0.2, "evidence_quality": 0.2, "testability": 0.2,
            "reversibility": 0.2, "source_reliability": 0.2, "scope_control": 0.2,
        }
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.INSPECT_FIRST


def test_stale_arch_map_downgrades_proceed_to_inspect_first() -> None:
    # unresolved-stale architecture evidence: PEBRA can't trust blast/criticality -> slow down.
    from pebra.core.constants import GraphFreshness
    from pebra.core.models import ArchitectureEvidence
    a = _assess(architecture_evidence=ArchitectureEvidence(graph_freshness=GraphFreshness.STALE))
    result = de.decide(a)
    assert result.recommended_decision is Decision.INSPECT_FIRST
    assert any(g["name"] == "stale_architecture_map" for g in result.gates_fired)


def test_rebuilt_and_fresh_and_unknown_arch_map_still_proceed() -> None:
    from pebra.core.constants import GraphFreshness
    from pebra.core.models import ArchitectureEvidence
    for state in (GraphFreshness.FRESH, GraphFreshness.REBUILT, GraphFreshness.UNKNOWN):
        a = _assess(architecture_evidence=ArchitectureEvidence(graph_freshness=state))
        assert de.decide(a).recommended_decision is Decision.PROCEED


def test_stale_arch_map_does_not_preempt_a_more_severe_gate() -> None:
    # gate 3 (expected_loss over threshold) ask_human must win over the stale-map inspect_first
    from pebra.core.constants import GraphFreshness
    from pebra.core.models import ArchitectureEvidence
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        architecture_evidence=ArchitectureEvidence(graph_freshness=GraphFreshness.STALE),
    )
    assert de.decide(a).recommended_decision is Decision.ASK_HUMAN


def test_stale_arch_map_gate_can_be_disabled_by_threshold() -> None:
    from dataclasses import replace
    from pebra.core.constants import GraphFreshness
    from pebra.core.models import ArchitectureEvidence
    inp = replace(
        _worked_example_input(),
        architecture_evidence=ArchitectureEvidence(graph_freshness=GraphFreshness.STALE),
        thresholds={**_worked_example_input().thresholds, "inspect_on_stale_arch_map": False},
    )
    assert de.decide(ab.build_assessment(inp)).recommended_decision is Decision.PROCEED


def test_stale_arch_map_is_recorded_even_when_a_higher_gate_decides() -> None:
    # evidence-validity observability: if gate 8 (low confidence) drives the decision but the arch map
    # is also stale, the audit trail must still record the stale-evidence fact.
    from pebra.core.constants import GraphFreshness
    from pebra.core.models import ArchitectureEvidence
    a = _assess(
        edit_confidence_factors={
            "p_success": 0.2, "evidence_quality": 0.2, "testability": 0.2,
            "reversibility": 0.2, "source_reliability": 0.2, "scope_control": 0.2,
        },
        architecture_evidence=ArchitectureEvidence(graph_freshness=GraphFreshness.STALE),
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.INSPECT_FIRST
    assert any(g["name"] == "low_edit_confidence" for g in result.gates_fired)
    assert any(g["name"] == "stale_architecture_map" for g in result.gates_fired)


# --- Gate 13: codegraph evidence-validity (mirrors Gate 12 stale-arch-map) ---


def _cg(**kw):
    from pebra.core.models import FanInEvidence
    return FanInEvidence(**kw)


def _require_cg(inp):
    from dataclasses import replace
    return replace(inp, thresholds={**inp.thresholds, "require_graph": True})


def test_gate13_untrusted_codegraph_downgrades_proceed_to_inspect_first() -> None:
    a = _assess(
        thresholds={**_worked_example_input().thresholds, "require_graph": True},
        fanin_evidence=_cg(
            resolution_method="unresolved", graph_freshness="stale",
            fallback_reason="codegraph worktree mismatch; run: pebra setup-graph --fix",
        ),
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.INSPECT_FIRST
    g13 = next(g for g in result.gates_fired if g.get("gate") == 13)
    assert g13["name"] == "fanin_evidence_invalid"
    assert "setup-graph --fix" in g13["reason"]
    assert result.fanin_validity["reason"]


def test_gate13_fails_clear_when_required_but_no_evidence_produced() -> None:
    # require_graph set but the provider was never wired (fanin_evidence stays None):
    # absence of REQUIRED evidence must fail CLEAR (inspect_first), never fail open to proceed.
    a = _assess(thresholds={**_worked_example_input().thresholds, "require_graph": True})
    result = de.decide(a)
    assert result.recommended_decision is Decision.INSPECT_FIRST
    g13 = next(g for g in result.gates_fired if g.get("gate") == 13)
    assert "setup-graph" in g13["reason"]
    assert result.fanin_validity["reason"]


def test_gate13_does_not_fire_when_codegraph_optional() -> None:
    # require_graph not set (default): an untrusted graph is silently optional -> proceed
    a = _assess(fanin_evidence=_cg(resolution_method="unresolved", graph_freshness="stale"))
    result = de.decide(a)
    assert result.recommended_decision is Decision.PROCEED
    assert not any(g.get("gate") == 13 for g in result.gates_fired)


def test_gate13_does_not_fire_when_trusted() -> None:
    a = _assess(
        thresholds={**_worked_example_input().thresholds, "require_graph": True},
        fanin_evidence=_cg(resolution_method="location", graph_freshness="fresh",
                                     symbol_fan_in_percentile=0.5),
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.PROCEED
    assert not any(g.get("gate") == 13 for g in result.gates_fired)


def test_gate13_does_not_preempt_a_more_severe_gate() -> None:
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,  # gate 3 ask_human
        thresholds={**_worked_example_input().thresholds, "require_graph": True},
        fanin_evidence=_cg(resolution_method="unresolved", graph_freshness="stale"),
    )
    result = de.decide(a)
    assert result.recommended_decision is Decision.ASK_HUMAN  # gate 3 wins


def test_gate13_recorded_as_advisory_when_a_higher_gate_decides() -> None:
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        thresholds={**_worked_example_input().thresholds, "require_graph": True},
        fanin_evidence=_cg(resolution_method="unresolved", graph_freshness="stale"),
    )
    result = de.decide(a)
    g13 = next(g for g in result.gates_fired if g.get("gate") == 13)
    assert g13.get("advisory") is True


# --- Gate 14: large repo-relative CodeGraph blast (conservative inspect guardrail) ---


def test_gate14_large_repo_blast_downgrades_proceed_to_inspect_first() -> None:
    a = _assess(
        fanin_evidence=_cg(
            resolution_method="location",
            graph_freshness="fresh",
            modify_transitive_impact_count=80,
            modify_repo_blast_fraction=0.40,
            modify_repo_graph_node_count=200,
        ),
    )
    result = de.decide(a)

    assert result.recommended_decision is Decision.INSPECT_FIRST
    g14 = next(g for g in result.gates_fired if g.get("gate") == 14)
    assert g14["name"] == "large_repo_blast_fraction"
    assert g14["modify_repo_blast_fraction"] == pytest.approx(0.40)
    assert g14["repo_node_count"] == 200


def test_gate14_requires_min_repo_node_count_and_trusted_graph() -> None:
    small_repo = de.decide(_assess(
        fanin_evidence=_cg(
            resolution_method="location",
            graph_freshness="fresh",
            modify_repo_blast_fraction=0.90,
            modify_repo_graph_node_count=49,
        ),
    ))
    parse_error = de.decide(_assess(
        fanin_evidence=_cg(
            resolution_method="location",
            graph_freshness="fresh",
            graph_file_error_count=1,
            modify_repo_blast_fraction=0.90,
            modify_repo_graph_node_count=200,
        ),
    ))

    assert small_repo.recommended_decision is Decision.PROCEED
    assert parse_error.recommended_decision is Decision.PROCEED
    assert not any(g.get("gate") == 14 for g in small_repo.gates_fired)
    assert not any(g.get("gate") == 14 for g in parse_error.gates_fired)


def test_gate14_fires_at_exact_min_repo_node_count_boundary() -> None:
    result = de.decide(_assess(
        fanin_evidence=_cg(
            resolution_method="location",
            graph_freshness="fresh",
            modify_repo_blast_fraction=0.40,
            modify_repo_graph_node_count=50,
        ),
    ))

    assert result.recommended_decision is Decision.INSPECT_FIRST
    assert any(g.get("gate") == 14 for g in result.gates_fired)


def test_gate14_recorded_as_advisory_when_higher_gate_decides() -> None:
    a = _assess(
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,
        fanin_evidence=_cg(
            resolution_method="location",
            graph_freshness="fresh",
            modify_repo_blast_fraction=0.50,
            modify_repo_graph_node_count=200,
        ),
    )
    result = de.decide(a)

    assert result.recommended_decision is Decision.ASK_HUMAN
    g14 = next(g for g in result.gates_fired if g.get("gate") == 14)
    assert g14.get("advisory") is True


def test_gate1_policy_violation_rejects_before_threshold() -> None:
    # even with a perfectly safe assessment, a policy violation rejects first
    result = de.decide(_assess(), policy_violations=["forbidden_path_edit"])
    assert result.recommended_decision is Decision.REJECT
    assert result.gates_fired[0]["gate"] == 1


def test_sanction_never_overrides_policy_violation_gate1() -> None:
    sanction = {"valid": True, "pre_edit_authorization_controls_satisfied": True,
                "converts_gates": [1, 2, 3, 4]}
    inp = replace(_worked_example_input(), sanction=sanction)
    result = de.decide(ab.build_assessment(inp), policy_violations=["forbidden_path_edit"])
    assert result.recommended_decision is Decision.REJECT
    assert result.risk_mode is not RiskMode.CONTROLLED_HIGH_RISK


def test_valid_sanction_converts_gate3_to_controlled_high_risk_proceed() -> None:
    sanction = {"valid": True, "pre_edit_authorization_controls_satisfied": True,
                "converts_gates": [2, 3, 4],
                "high_risk_triggers": [{"trigger_id": "hrt_001", "risk_class": "payment_side_effect"}]}
    from dataclasses import replace as _replace
    inp = _replace(
        _worked_example_input(),
        events=[{"event": "test_regression", "p_event": 0.60, "elicited_disutility": 0.40}],
        immediate_benefit=2.0,  # expected_loss 0.24 > 0.20 -> gate 3 ask_human; EU > 0
        sanction=sanction,
    )
    result = de.decide(ab.build_assessment(inp))
    assert result.recommended_decision is Decision.PROCEED
    assert result.risk_mode is RiskMode.CONTROLLED_HIGH_RISK
    assert result.requires_confirmation is True
    assert result.high_risk_triggers


def test_gate7_reachable_at_attempt_1_only_with_cap_2() -> None:
    # P4 gate-7-reachability (real decision engine, not a fake): the narrowed 2nd resubmission carrying
    # a verified candidate reaches gate 7 -> PROCEED ONLY when the cap is raised to 2. At cap 1 (plain
    # PEBRA) the revise_safer budget is exhausted first and gate 7 is unreachable.
    base = _verified_candidate_input(verified_patch_hash=de.candidate_patch_hash(_CANDIDATE_PATCH))

    cap2 = replace(base, thresholds={
        **base.thresholds, "revise_safer_attempt": 1, "max_revise_safer_attempts": 2})
    r2 = de.decide(ab.build_assessment(cap2))
    assert r2.recommended_decision is Decision.PROCEED
    assert any(g["name"] == "candidate_verification_passed" for g in r2.gates_fired)

    cap1 = replace(base, thresholds={
        **base.thresholds, "revise_safer_attempt": 1, "max_revise_safer_attempts": 1})
    r1 = de.decide(ab.build_assessment(cap1))
    assert r1.recommended_decision is not Decision.PROCEED  # exhausted -> gate 7 never reached
    assert not any(g["name"] == "candidate_verification_passed" for g in r1.gates_fired)
