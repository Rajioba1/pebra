"""Architecture §11, plan §5 — assess_controller end-to-end over FAKE ports (no FS/DB/subprocess).

This is the controller pipeline test: request -> ports gather evidence -> engine -> render/persist.
It must reproduce the spec §10 worked example end-to-end.
"""

from __future__ import annotations

import pytest

from pebra.app import assess_controller as ac
from pebra.core import models as m
from pebra.core.constants import Decision, RiskMode
from pebra.core.language_capability import LanguageCapability
from pebra.ports.repository_registry_port import RepoMetadata

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45,
    "c3_max_expected_loss_without_human": 0.20,
    "max_utility_sd_without_human": 0.20,
    "high_edit_confidence": 0.75,
    "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}


class FakeEvidence:
    def __init__(self, *, policy_violations=None):
        self.policy_violations = list(policy_violations or [])

    def gather_evidence(self, request, action, repo_root):
        return m.EvidenceBundle(
            events=[
                {"event": "test_regression", "p_event": 0.10, "elicited_disutility": 0.40},
                {"event": "public_api_break", "p_event": 0.03, "elicited_disutility": 0.80},
                {"event": "security_sensitive_change", "p_event": 0.04, "elicited_disutility": 0.90},
            ],
            p_success=0.74,
            immediate_benefit=0.82,
            review_cost=0.12,
            criticality_stage="C3",
            criticality_value=0.80,
            edit_confidence_factors={
                "p_success": 0.74, "evidence_quality": 0.78, "testability": 0.80,
                "reversibility": 0.92, "source_reliability": 0.86, "scope_control": 0.92,
            },
            thresholds=_THRESHOLDS,
            variance_breakdown={
                "p_success": 0.0016, "benefit": 0.0004, "event_losses": 0.0009,
                "review_cost": 0.0004, "scenario_variance": 0.0003,
            },
            benefit_delta_evidence=m.BenefitDeltaEvidence(source_type="projected"),
            policy_violations=self.policy_violations,
        )


class FakeSymbolDiff:
    def symbol_diff(self, action, repo_root):
        return m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/auth.py::validate_login"],
            max_change_kind="BEHAVIORAL",
            visibility="internal",
            symbol_fan_in_percentile=0.42,
            consequential_symbol_changed=False,
        )


class FakeBlast:
    def blast(self, action, repo_root):
        return m.BlastEvidence(direct_count=2, transitive_count=1)


class FakeSanction:
    def active_sanction(self, repo_id, action):
        return None

    def create_sanction(self, repo_id, sanction):
        return "sx_1"


class FakeRegistry:
    def resolve(self, start_path):
        return RepoMetadata(repo_id="repo_local_example", repo_root="/abs/path/to/example-repo")


class FakeStore:
    def __init__(self):
        self.persisted = []

    def persist_assessment(self, result, request_payload, predictions=None):
        self.persisted.append((result, request_payload, predictions or []))
        return f"asm_{len(self.persisted)}"

    def validate_chain(self):
        return True


class FakeSnapshotRead:
    def __init__(self):
        self.calls = 0

    def load_active_snapshot(self, repo_id):
        assert repo_id == "repo_local_example"
        self.calls += 1
        return None


def _request():
    return m.AssessmentRequest.single_action(
        task="Fix failing login validation",
        action_id="a1",
        label="Patch validate_login only",
        action_type="edit",
        affected_symbols=["src/auth.py::validate_login"],
        expected_files=["src/auth.py"],
    )


def _request_with_patch():
    return m.AssessmentRequest.single_action(
        task="Fix failing login validation",
        action_id="a1",
        label="Patch validate_login only",
        action_type="edit",
        affected_symbols=["src/auth.py::validate_login"],
        expected_files=["src/auth.py"],
        proposed_patch=(
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )


def _multi_request():
    return m.AssessmentRequest(
        task="Compare two edits",
        candidate_actions=[
            m.CandidateAction(
                id="a1", label="Patch validate_login", action_type="edit",
                affected_symbols=["src/auth.py::validate_login"], expected_files=["src/auth.py"],
            ),
            m.CandidateAction(
                id="a2", label="Patch session timeout", action_type="edit",
                affected_symbols=["src/session.py::timeout"], expected_files=["src/session.py"],
            ),
        ],
    )


def _run():
    store = FakeStore()
    outcome = ac.assess(
        _request(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
    )
    return outcome, store


def test_controller_reproduces_worked_example_decision() -> None:
    outcome, _ = _run()
    r = outcome.recommended_result
    assert r.recommended_decision is Decision.PROCEED
    assert r.requires_confirmation is True
    assert r.risk_mode is RiskMode.SENSITIVE_CONTEXT


def test_controller_reproduces_worked_example_numbers() -> None:
    outcome, _ = _run()
    s = outcome.recommended_result.scores
    assert s["expected_loss"] == pytest.approx(0.10)
    assert s["expected_utility"] == pytest.approx(0.3868)
    assert s["utility_sd"] == pytest.approx(0.06)
    assert s["rau"] == pytest.approx(0.31)
    assert round(s["edit_confidence"], 2) == 0.83
    assert s["risk_budget_used"] == pytest.approx(0.50)


def test_controller_renders_card_fields_and_packet() -> None:
    outcome, _ = _run()
    assert outcome.recommended_explanation.risk_level_band == "Moderate"
    assert outcome.recommended_explanation.value_after_risk_band == "Positive"
    assert outcome.recommended_result.model_guidance_packet["decision"] == "proceed"


def test_controller_persists_and_returns_repo_scope() -> None:
    outcome, store = _run()
    assert outcome.assessment_id == "asm_1"
    assert outcome.repo_id == "repo_local_example"
    assert len(store.persisted) == 1


def test_snapshot_read_loaded_once_per_assess_not_per_action() -> None:
    store = FakeStore()
    snapshot_read = FakeSnapshotRead()
    outcome = ac.assess(
        _multi_request(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        snapshot_read_port=snapshot_read,
    )
    assert len(outcome.scored_actions) == 2
    assert snapshot_read.calls == 1


class _FakeStructuralFeatures:
    def build_features(self, inp):
        return {"schema_version": 1, "symbol": {"is_public_api": True}}


def test_structural_features_captured_without_changing_scores() -> None:
    # Hard Rule: structural features are CAPTURE only — attaching a rich payload must not change any
    # score/decision; and the payload must be persisted on every prediction row.
    baseline, _ = _run()
    store = FakeStore()
    outcome = ac.assess(
        _request(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        structural_feature_provider=_FakeStructuralFeatures(),
    )
    assert outcome.recommended_result.scores == baseline.recommended_result.scores
    assert outcome.recommended_result.recommended_decision is baseline.recommended_result.recommended_decision
    _, _, predictions = store.persisted[0]
    assert predictions and all(
        p["features"] == {"schema_version": 1, "symbol": {"is_public_api": True}} for p in predictions
    )


class FakeFanInProvider:
    def __init__(self, ev):
        self.ev = ev

    def fanin(self, action, repo_root):
        return self.ev


def _run_cg(ev, extra_thresholds=None):
    store = FakeStore()
    outcome = ac.assess(
        _request(),
        thresholds={**_THRESHOLDS, **(extra_thresholds or {})},
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        fanin_provider=FakeFanInProvider(ev),
    )
    return outcome


class _UnparsedSymbolDiff:
    """A language with NO AST-level diff (e.g. C#): the symbol-diff provider returns UNKNOWN with
    parsed_patch_available=False — exactly the case the codegraph_structural tier is meant to upgrade."""

    def symbol_diff(self, action, repo_root):
        return m.SymbolDiffEvidence(parsed_patch_available=False, max_change_kind="UNKNOWN")


def _run_cg_unparsed(ev, request=None):
    store = FakeStore()
    return ac.assess(
        request or _request(), thresholds=_THRESHOLDS, start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(), symbol_diff_provider=_UnparsedSymbolDiff(),
        blast_provider=FakeBlast(), sanction_port=FakeSanction(), repository_registry=FakeRegistry(),
        store=store, fanin_provider=FakeFanInProvider(ev),
    )


class FakeCapabilityProvider:
    def __init__(self, cap):
        self.cap = cap

    def capability_for(self, language, repo_root):
        return self.cap


def _run_cg_unparsed_with_cap(ev, cap, request=None):
    store = FakeStore()
    return ac.assess(
        request or _request(), thresholds=_THRESHOLDS, start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(), symbol_diff_provider=_UnparsedSymbolDiff(),
        blast_provider=FakeBlast(), sanction_port=FakeSanction(), repository_registry=FakeRegistry(),
        store=store, fanin_provider=FakeFanInProvider(ev),
        language_capability_provider=FakeCapabilityProvider(cap),
    )


def test_codegraph_structural_tier_upgrades_unknown_for_exported_owner() -> None:
    # C#-shaped: no AST diff, but the graph resolved an EXPORTED owner -> coarse CONTRACT (not UNKNOWN),
    # tagged structure_tier=codegraph_structural. This is the multi-language breadth unlock.
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("cs:Render",), resolved_qualified_names=("Ns.Widget::Render",),
        resolved_symbol_count=1, symbol_fan_in_percentile=0.5, is_exported_contract=True,
    )
    sse = _run_cg_unparsed(ev, _request_with_patch()).recommended_result.symbol_scope_evidence
    assert sse["max_change_kind"] == "CONTRACT"
    assert sse["visibility"] == "exported"
    assert sse["scope_basis"] == "graph_identity"


def test_codegraph_structural_tier_requires_partial_or_full_capability() -> None:
    # A trusted location fan-in result is still only graph-risk evidence when the measured language
    # capability lacks callable visibility/signature coverage. It must not fabricate a changed-symbol
    # diff tier for a risk-only language.
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh", resolved_language="csharp",
        node_ids_resolved=("cs:Render",), resolved_qualified_names=("Ns.Widget::Render",),
        resolved_symbol_count=1, symbol_fan_in_percentile=0.5, is_exported_contract=True,
    )
    cap = LanguageCapability(
        language="csharp", probe_status="measured", node_count=12,
        signature_coverage_ratio=0.0, visibility_coverage_ratio=0.0,
    )
    sse = _run_cg_unparsed_with_cap(ev, cap, _request_with_patch()).recommended_result.symbol_scope_evidence
    assert sse["max_change_kind"] == "UNKNOWN"
    assert sse["structure_tier"] == "unavailable"


def test_codegraph_structural_tier_rejects_mixed_language_resolution() -> None:
    # A patch that resolves to multiple languages may still have graph-risk evidence, but it must not
    # be collapsed into one fabricated structural diff row.
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("cs:Render", "ts:render"),
        resolved_qualified_names=("Ns.Widget::Render", "render"),
        resolved_symbol_count=2, symbol_fan_in_percentile=0.5, is_exported_contract=True,
        resolved_languages=("csharp", "typescript"),
    )
    cap = LanguageCapability(
        language="mixed", probe_status="unmeasured", fallback_reason="multiple resolved languages"
    )
    sse = _run_cg_unparsed_with_cap(ev, cap, _request_with_patch()).recommended_result.symbol_scope_evidence
    assert sse["max_change_kind"] == "UNKNOWN"
    assert sse["structure_tier"] == "unavailable"


def test_codegraph_structural_tier_requires_a_candidate_patch() -> None:
    # A no-patch request may name affected_symbols for context, but that does NOT prove an owner body
    # was touched. The coarse graph tier must not turn name-fallback fan-in into a fabricated body edit.
    ev = m.FanInEvidence(
        resolution_method="name_fallback", graph_freshness="fresh",
        node_ids_resolved=("cs:Render",), resolved_qualified_names=("Ns.Widget::Render",),
        resolved_symbol_count=1, symbol_fan_in_percentile=0.5, is_exported_contract=True,
    )
    sse = _run_cg_unparsed(ev).recommended_result.symbol_scope_evidence
    assert sse["max_change_kind"] == "UNKNOWN"
    assert sse["structure_tier"] == "unavailable"


def test_codegraph_structural_tier_skipped_when_ast_diff_present() -> None:
    # When a real AST diff IS present (parsed_patch_available=True), the coarse tier must NOT fire —
    # the Python path is byte-identical (BEHAVIORAL from FakeSymbolDiff, not overwritten).
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("x",), resolved_qualified_names=("x",), resolved_symbol_count=1,
        is_exported_contract=True,
    )
    sse = _run_cg(ev).recommended_result.symbol_scope_evidence
    assert sse["max_change_kind"] == "BEHAVIORAL"  # unchanged; coarse tier did not override the AST


def test_no_fanin_provider_leaves_fan_in_at_evidence_value() -> None:
    # Without codegraph, the symbol fan-in is whatever the symbol-diff provider supplied (0.42 here).
    outcome, _ = _run()
    assert outcome.recommended_result.symbol_scope_evidence["symbol_fan_in_percentile"] == pytest.approx(0.42)


def test_trusted_high_fanin_patches_percentile_and_marks_consequential() -> None:
    ev = m.FanInEvidence(
        symbol_fan_in_percentile=0.95, symbol_caller_count=12,
        resolution_method="location", graph_freshness="fresh",
    )
    sse = _run_cg(ev).recommended_result.symbol_scope_evidence
    assert sse["symbol_fan_in_percentile"] == pytest.approx(0.95)  # codegraph value, not 0.42
    assert sse["consequential_symbol_changed"] is True  # high fan-in on a BEHAVIORAL change escalates


def test_codegraph_versions_are_provenance_not_scores() -> None:
    ev = m.FanInEvidence(
        symbol_fan_in_percentile=0.95, symbol_caller_count=12,
        resolution_method="location", graph_freshness="fresh",
        provider_version="1.1.1", index_version="24",
    )
    result = _run_cg(ev).recommended_result

    assert result.provenance["graph_provenance"] == {
        "engine": "CodeGraph",
        "provider_version": "1.1.1",
        "index_version": "24",
        # which structural tier classified this diff (no resolved_language here -> no capability block)
        "structure_tier": "unavailable",
    }
    symbol_fanin = result.scores["symbol_scope_evidence"]["symbol_fanin"]
    assert "provider_version" not in symbol_fanin
    assert "index_version" not in symbol_fanin


def test_trusted_low_fanin_patches_percentile_without_forcing_consequential() -> None:
    ev = m.FanInEvidence(
        symbol_fan_in_percentile=0.10, resolution_method="location", graph_freshness="fresh",
    )
    sse = _run_cg(ev).recommended_result.symbol_scope_evidence
    assert sse["symbol_fan_in_percentile"] == pytest.approx(0.10)
    assert sse["consequential_symbol_changed"] is False


def test_untrusted_graph_routes_to_inspect_first_via_gate13_only_when_required() -> None:
    baseline = _run()[0].recommended_result
    assert baseline.recommended_decision is Decision.PROCEED
    stale = m.FanInEvidence(
        resolution_method="unresolved", graph_freshness="stale",
        fallback_reason="codegraph worktree mismatch; run: pebra setup-graph --fix",
    )
    # required: untrusted graph is an INFRASTRUCTURE-validity failure -> Gate 13 inspect_first, with the
    # actionable remediation surfaced — NOT a nuked edit_confidence (the edit itself is unchanged).
    required = _run_cg(stale, {"require_graph": True}).recommended_result
    assert required.recommended_decision is Decision.INSPECT_FIRST
    assert required.scores["edit_confidence"] == pytest.approx(baseline.scores["edit_confidence"])
    g13 = next(g for g in required.gates_fired if g.get("gate") == 13)
    assert "setup-graph --fix" in g13["reason"]
    assert required.fanin_validity["reason"] == g13["reason"]
    # the remediation must actually reach the model-facing guidance packet, not just gates_fired
    advisory = required.model_guidance_packet["advisory"]
    assert any("setup-graph --fix" in s for s in advisory["suggested_inspection"])
    assert advisory["fanin_validity"]["reason"] == g13["reason"]
    # not required (default): codegraph is optional -> identity, golden preserved
    optional = _run_cg(stale).recommended_result
    assert optional.recommended_decision is Decision.PROCEED
    assert optional.scores["edit_confidence"] == pytest.approx(baseline.scores["edit_confidence"])


def test_ambiguous_name_match_is_not_trusted_fanin() -> None:
    ev = m.FanInEvidence(
        resolution_method="name_fallback_ambiguous", node_ids_resolved=("x", "y"),
        graph_freshness="fresh",
    )
    sse = _run_cg(ev, {"require_graph": True}).recommended_result.symbol_scope_evidence
    # ambiguous never patches a trusted fan-in: the percentile stays the symbol-diff value
    assert sse["symbol_fan_in_percentile"] == pytest.approx(0.42)


def test_controller_rejects_invalid_request() -> None:
    from pebra.core.request_validator import RequestValidationError
    bad = m.AssessmentRequest(task="", candidate_actions=[])
    with pytest.raises(RequestValidationError):
        ac.assess(
            bad, thresholds=_THRESHOLDS, start_path="/x",
            evidence_provider=FakeEvidence(), symbol_diff_provider=FakeSymbolDiff(),
            blast_provider=FakeBlast(), sanction_port=FakeSanction(),
            repository_registry=FakeRegistry(), store=FakeStore(),
        )


def test_policy_violations_come_from_evidence_provider_not_request() -> None:
    request = _request()
    request.evidence["policy_violations"] = ["request_supplied_bypass_vector"]
    store = FakeStore()
    outcome = ac.assess(
        request,
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(policy_violations=["configured_forbidden_path"]),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
    )
    assert outcome.recommended_result.recommended_decision is Decision.REJECT
    assert outcome.recommended_result.gates_fired[0]["detail"] == ["configured_forbidden_path"]


class FakeRevisionEvidence:
    def gather_evidence(self, request, action, repo_root):
        return m.EvidenceBundle(
            events=[{"event": "dependency_break", "p_event": 0.60, "elicited_disutility": 0.40}],
            p_success=0.74,
            immediate_benefit=2.0,
            review_cost=0.12,
            criticality_stage="C3",
            criticality_value=0.80,
            edit_confidence_factors={
                "p_success": 0.74, "evidence_quality": 0.78, "testability": 0.80,
                "reversibility": 0.92, "source_reliability": 0.86, "scope_control": 0.92,
            },
            thresholds=_THRESHOLDS,
            variance_breakdown={
                "p_success": 0.0016, "benefit": 0.0004, "event_losses": 0.0009,
                "review_cost": 0.0004, "scenario_variance": 0.0003,
            },
            benefit_delta_evidence=m.BenefitDeltaEvidence(source_type="projected"),
        )


class FakeRevisionSymbolDiff:
    def symbol_diff(self, action, repo_root):
        return m.SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/api.py::public_fn", "src/api.py::helper"],
            max_change_kind="CONTRACT",
            visibility="public_api",
            symbol_fan_in_percentile=0.95,
            consequential_symbol_changed=True,
        )


class FakeRevisionAttemptStore(FakeStore):
    def __init__(self, count: int):
        super().__init__()
        self.count = count
        self.count_calls = []

    def revise_safer_attempt_count(self, repo_id, assessed_commit, target_files):
        self.count_calls.append((repo_id, assessed_commit, tuple(target_files)))
        return self.count


class FakeFailingRevisionAttemptStore(FakeStore):
    def revise_safer_attempt_count(self, repo_id, assessed_commit, target_files):
        raise RuntimeError("store unavailable")


def test_assess_uses_persisted_revise_safer_attempt_when_caller_is_lower() -> None:
    store = FakeRevisionAttemptStore(count=1)
    outcome = ac.assess(
        _request(),
        thresholds={
            **_THRESHOLDS,
            "revise_safer_enabled": True,
            "revise_safer_attempt": 0,
            "max_revise_safer_attempts": 1,
        },
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeRevisionEvidence(),
        symbol_diff_provider=FakeRevisionSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        assessed_commit="abc123",
    )

    assert store.count_calls == [("repo_local_example", "abc123", ("src/auth.py",))]
    assert outcome.recommended_result.recommended_decision is Decision.ASK_HUMAN
    assert not any(g.get("name") == "revise_safer" for g in outcome.recommended_result.gates_fired)
    _result, persisted_payload, _predictions = store.persisted[0]
    assert persisted_payload["thresholds"]["revise_safer_attempt"] == 1


def test_assess_keeps_caller_revise_safer_attempt_when_caller_is_higher() -> None:
    store = FakeRevisionAttemptStore(count=0)
    outcome = ac.assess(
        _request(),
        thresholds={
            **_THRESHOLDS,
            "revise_safer_enabled": True,
            "revise_safer_attempt": 1,
            "max_revise_safer_attempts": 1,
        },
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeRevisionEvidence(),
        symbol_diff_provider=FakeRevisionSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        assessed_commit="abc123",
    )

    assert store.count_calls == [("repo_local_example", "abc123", ("src/auth.py",))]
    assert outcome.recommended_result.recommended_decision is Decision.ASK_HUMAN
    _result, persisted_payload, _predictions = store.persisted[0]
    assert persisted_payload["thresholds"]["revise_safer_attempt"] == 1


def test_assess_store_attempt_error_fails_open_to_caller_attempt() -> None:
    outcome = ac.assess(
        _request(),
        thresholds={
            **_THRESHOLDS,
            "revise_safer_enabled": True,
            "revise_safer_attempt": 1,
            "max_revise_safer_attempts": 1,
        },
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeRevisionEvidence(),
        symbol_diff_provider=FakeRevisionSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=FakeFailingRevisionAttemptStore(),
        assessed_commit="abc123",
    )

    assert outcome.recommended_result.recommended_decision is Decision.ASK_HUMAN
