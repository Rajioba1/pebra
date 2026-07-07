"""Architecture §9, AD-11/AD-27 — post_assessment_guardrails: pure GuardrailResult evaluation.

The agent may proceed on a branch, but only if the final diff still matches the approved envelope.
This module is pure: it receives already-fetched diff/HEAD/contract data and returns the decision.
Hard failures map to existing decisions only (no new enum).
"""

from __future__ import annotations

from pebra.core import post_assessment_guardrails as pag
from pebra.core.constants import Decision


def _clean_input(**overrides):
    """A baseline within-envelope verify that should yield proceed; override per case."""
    base = dict(
        assessed_commit="abc123",
        current_head="abc123",
        safe_scope_files=["src/auth.py", "src/auth/__tests__/**"],
        changed_files=["src/auth.py"],
        dependency_changed=False,
        schema_changed=False,
        migration_changed=False,
        pre_edit_max_change_kind="BEHAVIORAL",
        actual_max_change_kind="BEHAVIORAL",
        actual_changed_symbols=["src/auth.py::validate_login"],
        contract_surface_changes=[],
        risky_scope=[
            {"change": "dependency upgrades", "action": "requires_reassessment",
             "signal": "dependency_changed"},
            {"change": "schema changes", "action": "requires_reassessment", "signal": "schema_changed"},
        ],
        triggered_signals=set(),
        required_checks=["pytest -q src/auth"],
        completed_checks={"pytest -q src/auth": "passed"},
        requires_dry_run=False,
        dry_run_preview_present=False,
        policy_forbidden=False,
    )
    base.update(overrides)
    return pag.GuardrailInput(**base)


def test_newly_consequential_post_edit_routes_inspect_first() -> None:
    # actual change is consequential by post-edit fan-in, but the pre-edit assessment didn't flag it:
    # softer than a kind-severity increase -> inspect_first, with the new flag set.
    r = pag.evaluate(_clean_input(actual_consequential=True, pre_edit_consequential=False))
    assert r.newly_consequential is True
    assert r.pre_commit_decision is Decision.INSPECT_FIRST
    assert any("consequential" in reason.lower() for reason in r.reasons)


def test_already_consequential_pre_edit_is_not_re_flagged() -> None:
    # assess already flagged it consequential (and the agent proceeded under that approval): verify must
    # NOT re-escalate the same signal -> no newly_consequential, clean proceed.
    r = pag.evaluate(_clean_input(actual_consequential=True, pre_edit_consequential=True))
    assert r.newly_consequential is False
    assert r.pre_commit_decision is Decision.PROCEED


def test_not_consequential_post_edit_does_not_escalate() -> None:
    r = pag.evaluate(_clean_input(actual_consequential=False, pre_edit_consequential=False))
    assert r.newly_consequential is False
    assert r.pre_commit_decision is Decision.PROCEED


def test_within_envelope_verify_proceeds() -> None:
    r = pag.evaluate(_clean_input())
    assert r.pre_commit_decision is Decision.PROCEED
    assert r.scope_drift_detected is False
    assert r.evidence_freshness == "fresh"
    assert r.safe_scope_status == "ok"


def test_stale_evidence_routes_to_inspect_first() -> None:
    r = pag.evaluate(_clean_input(current_head="def456"))
    assert r.evidence_freshness == "stale"
    assert r.pre_commit_decision is Decision.INSPECT_FIRST
    assert any("stale" in reason.lower() for reason in r.reasons)


def test_unknown_evidence_when_no_assessed_commit() -> None:
    r = pag.evaluate(_clean_input(assessed_commit=None))
    assert r.evidence_freshness == "unknown"


def test_unknown_freshness_does_not_autonomously_proceed() -> None:
    # "cannot verify freshness" is not "safe to proceed" — it must route to inspect_first.
    r = pag.evaluate(_clean_input(assessed_commit=None))
    assert r.pre_commit_decision is Decision.INSPECT_FIRST
    r2 = pag.evaluate(_clean_input(current_head=None))
    assert r2.pre_commit_decision is Decision.INSPECT_FIRST


def test_unexpected_file_outside_safe_scope_is_scope_drift_inspect_first() -> None:
    r = pag.evaluate(_clean_input(changed_files=["src/auth.py", "src/unrelated/util.py"]))
    assert r.scope_drift_detected is True
    assert "src/unrelated/util.py" in r.unexpected_files
    assert r.pre_commit_decision is Decision.INSPECT_FIRST
    assert r.safe_scope_status == "violated"


def test_glob_in_safe_scope_matches_test_files() -> None:
    r = pag.evaluate(_clean_input(changed_files=["src/auth.py", "src/auth/__tests__/test_login.py"]))
    assert r.unexpected_files == []
    assert r.pre_commit_decision is Decision.PROCEED


def test_dependency_change_is_broad_drift_ask_human() -> None:
    r = pag.evaluate(
        _clean_input(dependency_changed=True, triggered_signals={"dependency_changed"})
    )
    assert r.pre_commit_decision is Decision.ASK_HUMAN
    assert "requires_reassessment" in r.risky_scope_actions_triggered


def test_broad_drift_marks_safe_scope_violated() -> None:
    # safe_scope covers dependencies (spec §12.3.1): broad drift must not read as "ok".
    r = pag.evaluate(
        _clean_input(dependency_changed=True, triggered_signals={"dependency_changed"})
    )
    assert r.safe_scope_status == "violated"
    assert r.scope_drift_detected is True


def test_dependency_requires_reassessment_is_not_double_reported() -> None:
    # broad drift (ask_human) is the headline; the default requires_reassessment risky_scope entry is
    # recorded as triggered but must NOT add a duplicate weaker reason.
    r = pag.evaluate(
        _clean_input(dependency_changed=True, triggered_signals={"dependency_changed"})
    )
    assert r.pre_commit_decision is Decision.ASK_HUMAN
    assert "requires_reassessment" in r.risky_scope_actions_triggered  # label kept
    assert not any("requires_reassessment change touched" in reason for reason in r.reasons)
    # exactly one reason about the dependency/broad-drift event
    assert sum("dependency" in reason.lower() for reason in r.reasons) == 1


def test_forbidden_on_broad_signal_still_rejects_despite_dedup() -> None:
    # dedup only applies to requires_reassessment; a forbidden entry on a broad signal must reject.
    r = pag.evaluate(
        _clean_input(
            migration_changed=True,
            triggered_signals={"migration_changed"},
            risky_scope=[{"change": "drop table", "action": "forbidden",
                          "signal": "migration_changed"}],
        )
    )
    assert r.pre_commit_decision is Decision.REJECT


def test_risky_scope_forbidden_rejects() -> None:
    r = pag.evaluate(
        _clean_input(
            risky_scope=[{"change": "delete payments table", "action": "forbidden",
                          "signal": "migration_changed"}],
            migration_changed=True,
            triggered_signals={"migration_changed"},
        )
    )
    assert r.pre_commit_decision is Decision.REJECT
    assert "forbidden" in r.risky_scope_actions_triggered


def test_symbol_reclassification_more_severe_is_drift_ask_human() -> None:
    # pre-edit said COSMETIC; actual diff is SIDE_EFFECT -> mismatch -> reassessment
    r = pag.evaluate(
        _clean_input(pre_edit_max_change_kind="COSMETIC", actual_max_change_kind="SIDE_EFFECT")
    )
    assert r.symbol_change_mismatch is True
    assert r.scope_drift_detected is True
    assert r.pre_commit_decision is Decision.ASK_HUMAN


def test_symbol_reclassification_less_severe_is_not_mismatch() -> None:
    r = pag.evaluate(
        _clean_input(pre_edit_max_change_kind="CONTRACT", actual_max_change_kind="BEHAVIORAL")
    )
    assert r.symbol_change_mismatch is False


def test_unknown_actual_kind_is_not_a_symbol_mismatch() -> None:
    # UNKNOWN is never a *mismatch* (that means "known to be more severe"). With no reclassification
    # attempt (e.g. a pure non-code change), it does not escalate on its own.
    r = pag.evaluate(
        _clean_input(pre_edit_max_change_kind="BEHAVIORAL", actual_max_change_kind="UNKNOWN")
    )
    assert r.symbol_change_mismatch is False
    assert r.pre_commit_decision is Decision.PROCEED


def test_unknown_actual_with_reclassification_attempted_escalates_inspect_first() -> None:
    # changed Python files that couldn't be classified (syntax error / unparseable) = "cannot prove
    # envelope compliance" -> must not silently proceed.
    r = pag.evaluate(
        _clean_input(actual_max_change_kind="UNKNOWN", reclassification_attempted=True)
    )
    assert r.symbol_change_mismatch is False
    assert r.pre_commit_decision is Decision.INSPECT_FIRST
    assert any("could not be classified" in reason for reason in r.reasons)


def test_unknown_with_reclassification_and_missing_check_is_test_first() -> None:
    # severity precedence: the missing-check (test_first) outranks the unclassifiable inspect_first
    r = pag.evaluate(
        _clean_input(
            actual_max_change_kind="UNKNOWN", reclassification_attempted=True, completed_checks={}
        )
    )
    assert r.pre_commit_decision is Decision.TEST_FIRST


def test_contract_surface_change_routes_to_ask_human() -> None:
    r = pag.evaluate(_clean_input(contract_surface_changes=["public_api_break:charge_customer"]))
    assert r.pre_commit_decision is Decision.ASK_HUMAN
    assert "public_api_break:charge_customer" in r.contract_surface_changes


def test_missing_required_check_routes_to_test_first() -> None:
    r = pag.evaluate(_clean_input(completed_checks={}))
    assert "pytest -q src/auth" in r.missing_checks
    assert r.pre_commit_decision is Decision.TEST_FIRST


def test_failed_required_check_routes_to_ask_human() -> None:
    r = pag.evaluate(_clean_input(completed_checks={"pytest -q src/auth": "failed"}))
    assert "pytest -q src/auth" in r.failed_checks
    assert r.pre_commit_decision is Decision.ASK_HUMAN


def test_dry_run_required_without_preview_inspect_first() -> None:
    r = pag.evaluate(_clean_input(requires_dry_run=True, dry_run_preview_present=False))
    assert r.dry_run_required is True
    assert r.pre_commit_decision is Decision.INSPECT_FIRST


def test_policy_forbidden_rejects() -> None:
    r = pag.evaluate(_clean_input(policy_forbidden=True))
    assert r.pre_commit_decision is Decision.REJECT


def test_decision_precedence_reject_dominates_ask_human_and_below() -> None:
    # missing check (test_first) + contract change (ask_human) + forbidden policy (reject) -> reject
    r = pag.evaluate(
        _clean_input(
            completed_checks={},
            contract_surface_changes=["route_behavior_break:/login"],
            policy_forbidden=True,
        )
    )
    assert r.pre_commit_decision is Decision.REJECT


def test_verify_decision_label_mirrors_pre_commit_decision() -> None:
    r = pag.evaluate(_clean_input(current_head="zzz"))
    assert r.verify_decision == r.pre_commit_decision.value


def test_semantic_approval_not_reproduced_by_verify_escalates() -> None:
    # assess approved via codegraph_semantic; verify could only reproduce the coarse tier -> INSPECT_FIRST
    result = pag.evaluate(_clean_input(
        pre_edit_structure_tier="codegraph_semantic",
        actual_structure_tier="codegraph_structural",
    ))
    assert result.pre_commit_decision is Decision.INSPECT_FIRST
    assert any("semantic" in r.lower() for r in result.reasons)


def test_semantic_approval_reproduced_does_not_escalate() -> None:
    result = pag.evaluate(_clean_input(
        pre_edit_structure_tier="codegraph_semantic",
        actual_structure_tier="codegraph_semantic",
    ))
    assert result.pre_commit_decision is Decision.PROCEED


def test_non_semantic_pre_edit_tier_is_unaffected() -> None:
    # the long-standing default/coarse pre-edit tiers never trip the new asymmetry rule
    for tier in ("unavailable", "codegraph_structural", "python_ast"):
        result = pag.evaluate(_clean_input(
            pre_edit_structure_tier=tier, actual_structure_tier="unavailable"))
        assert result.pre_commit_decision is Decision.PROCEED
