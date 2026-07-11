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


class FakeCandidateBinding:
    def bind_candidate(self, action, repo_root):
        assert action.id == "a1"
        assert repo_root == "/abs/path/to/example-repo"
        return {
            "algorithm": "sha256-normalized-content-v1",
            "files": {"src/auth.py": "a" * 64},
        }


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


def test_trusted_candidate_verification_sidecar_selected_by_action_id() -> None:
    request = _request_with_patch()
    raw = {
        "a1": {
            "status": "passed",
            "checks": {"targeted_tests": "passed"},
            "required_checks": ["targeted_tests"],
            "domain": "covering_tests",
            "verified_patch_hash": "a" * 64,
        }
    }

    verification = ac._trusted_verification_for_action(raw, request.candidate_actions[0])

    assert verification is not None
    assert verification.status == "passed"
    assert verification.required_checks == ["targeted_tests"]
    assert verification.verified_patch_hash == "a" * 64


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


def test_controller_persists_exact_candidate_binding_in_guidance_packet() -> None:
    store = FakeStore()
    outcome = ac.assess(
        _request_with_patch(),
        thresholds=_THRESHOLDS,
        start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(),
        symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(),
        sanction_port=FakeSanction(),
        repository_registry=FakeRegistry(),
        store=store,
        candidate_binding_provider=FakeCandidateBinding(),
    )

    binding = outcome.recommended_result.model_guidance_packet["binding"]["candidate"]
    assert binding["files"] == {"src/auth.py": "a" * 64}


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


def test_controller_surfaces_single_candidate_aggregate_from_owner_evidence() -> None:
    owners = (
        m.OwnerRiskEvidence(
            node_id="a", file_path="src/auth.py", language="python",
            impact_percentile=0.7, impacted_node_ids=("caller:shared",),
        ),
        m.OwnerRiskEvidence(
            node_id="b", file_path="src/helper.py", language="typescript",
            impact_percentile=0.6, impacted_node_ids=("caller:other",),
        ),
    )
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("a", "b"), resolved_symbol_count=2,
        resolved_languages=("python", "typescript"),
        resolved_file_paths=("src/auth.py", "src/helper.py"),
        modify_impact_count=2, modify_impact_percentile=0.8,
        owner_risk=owners,
    )
    request = m.AssessmentRequest.single_action(
        task="t", action_id="a", label="multi", action_type="edit",
        expected_files=["src/auth.py", "src/helper.py"],
    )

    result = _run_cg(ev, request=request).recommended_result

    assert result.scores["candidate_aggregate"]["file_count"] == 2
    assert result.scores["candidate_aggregate"]["owner_count"] == 2
    assert result.scores["candidate_aggregate"]["languages"] == ("python", "typescript")


def _run_cg(ev, extra_thresholds=None, request=None):
    store = FakeStore()
    outcome = ac.assess(
        request or _request(),
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


def test_language_capability_provenance_includes_measured_node_count() -> None:
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh", resolved_language="typescript",
        node_ids_resolved=("ts:f",), resolved_qualified_names=("f",),
        resolved_symbol_count=1, symbol_fan_in_percentile=0.5,
    )
    cap = LanguageCapability(
        language="typescript", probe_status="measured", node_count=12,
        signature_coverage_ratio=1.0, visibility_coverage_ratio=0.0,
    )
    result = _run_cg_unparsed_with_cap(ev, cap, _request_with_patch()).recommended_result

    assert result.provenance["graph_provenance"]["language_capability"]["node_count"] == 12


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

    def revise_safer_attempt_count(self, repo_id, assessed_commit, target_files, action_id=None, task=None):
        self.count_calls.append((repo_id, assessed_commit, tuple(target_files), action_id, task))
        return self.count


class FakeFailingRevisionAttemptStore(FakeStore):
    def revise_safer_attempt_count(self, repo_id, assessed_commit, target_files, action_id=None, task=None):
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

    assert store.count_calls == [
        ("repo_local_example", "abc123", ("src/auth.py",), "a1", "Fix failing login validation")
    ]
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

    assert store.count_calls == [
        ("repo_local_example", "abc123", ("src/auth.py",), "a1", "Fix failing login validation")
    ]
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


# --- P1: codegraph_semantic tier dispatch (dark-gated) ---


class _FakeMaterializedDiff:
    def __init__(self, result):
        self.result = result

    def diff_for_patch(self, *, repo_root, patch):
        return self.result

    def diff(self, **_kw):  # pragma: no cover - unused by the dispatch
        return self.result


def _full_cap():
    return LanguageCapability(
        language="typescript", probe_status="measured", node_count=100,
        signature_coverage_ratio=0.9, visibility_coverage_ratio=0.9)


def _semantic_ev():
    return m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh", node_ids_resolved=("ts:f",),
        resolved_qualified_names=("f",), resolved_language="typescript",
        resolved_languages=("typescript",), resolved_file_paths=("src/a.ts",),
        resolved_symbol_count=1, symbol_fan_in_percentile=0.5)


def _run_semantic(ev, materialized, *, enabled=True, provider=True, deployment_enabled=True):
    store = FakeStore()
    thr = {**_THRESHOLDS}
    if enabled:
        thr["codegraph_semantic_diff_enabled"] = 1.0
    return ac.assess(
        _request_with_patch(), thresholds=thr, start_path="/abs/path/to/example-repo/src",
        evidence_provider=FakeEvidence(), symbol_diff_provider=_UnparsedSymbolDiff(),
        blast_provider=FakeBlast(), sanction_port=FakeSanction(), repository_registry=FakeRegistry(),
        store=store, fanin_provider=FakeFanInProvider(ev),
        language_capability_provider=FakeCapabilityProvider(_full_cap()),
        materialized_diff_provider=_FakeMaterializedDiff(materialized) if provider else None,
        semantic_diff_enabled=deployment_enabled,
    )


def _sig_change_result():
    return m.MaterializedGraphDiffResult(
        available=True,
        rows=(m.MaterializedGraphDiffRow(
            file_path="src/a.ts", qualified_name="f", language="typescript",
            signature_changed=True, return_type_changed=False, visibility_changed=False),))


def test_semantic_tier_enriches_when_enabled_full_language() -> None:
    sse = _run_semantic(_semantic_ev(), _sig_change_result()).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_semantic"
    assert sse["max_change_kind"] == "CONTRACT"  # proven signature change


def test_semantic_tier_falls_back_to_structural_when_disabled() -> None:
    sse = _run_semantic(
        _semantic_ev(), _sig_change_result(), enabled=False).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_structural"  # flag off -> coarse, materializer unused


def test_semantic_tier_dark_without_provider_is_structural() -> None:
    # production default (composition does not wire the provider yet) -> coarse tier, byte-identical.
    sse = _run_semantic(
        _semantic_ev(), _sig_change_result(), provider=False).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_structural"


def test_semantic_tier_dark_without_deployment_gate_is_structural() -> None:
    sse = _run_semantic(
        _semantic_ev(), _sig_change_result(), deployment_enabled=False
    ).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_structural"


def test_semantic_tier_unavailable_result_falls_back_to_structural() -> None:
    unavailable = m.MaterializedGraphDiffResult(available=False, fallback_reason="patch did not apply")
    sse = _run_semantic(_semantic_ev(), unavailable).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_structural"


def test_semantic_tier_multi_owner_degrades_to_honest_structural_label() -> None:
    # False-provenance fix: materialized diff AVAILABLE but the patch resolved 2 owners -> the enrichment
    # degrades to the coarse floor (ambiguous join). The tier must be labeled codegraph_structural
    # (honest), NOT codegraph_semantic, since no signature-level check actually applied.
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("ts:f", "ts:g"), resolved_qualified_names=("f", "g"),
        resolved_language="typescript", resolved_languages=("typescript",),
        resolved_file_paths=("src/a.ts", "src/b.ts"), resolved_symbol_count=2,
        symbol_fan_in_percentile=0.5)
    sse = _run_semantic(ev, _sig_change_result()).recommended_result.symbol_scope_evidence
    assert sse["structure_tier"] == "codegraph_structural"  # degraded -> honest coarse label


def test_semantic_tier_enriches_added_abstract_member() -> None:
    ev = m.FanInEvidence(
        resolution_method="location", graph_freshness="fresh",
        node_ids_resolved=("ts:ZodType",), resolved_qualified_names=("ZodType",),
        resolved_language="typescript", resolved_languages=("typescript",),
        resolved_file_paths=("src/a.ts",), resolved_symbol_count=1,
        symbol_fan_in_percentile=0.5,
    )
    result = m.MaterializedGraphDiffResult(
        available=True,
        rows=(m.MaterializedGraphDiffRow(
            file_path="src/a.ts",
            qualified_name="ZodType._pebraDescribe",
            language="typescript",
            operation="added",
            kind="method",
            signature_changed=True,
            is_abstract=True,
        ),),
    )

    sse = _run_semantic(ev, result).recommended_result.symbol_scope_evidence

    assert sse["structure_tier"] == "codegraph_semantic"
    assert sse["max_change_kind"] == "CONTRACT"


# --- tier-3: derived future_change_exposure credits RCA benefit by default -----------------------

class _MeasuredBenefitEvidence:
    """FakeEvidence but with a MEASURED benefit delta (a simplification) + a settable exposure/explicit
    flag — simulates the post-RCA-merge bundle the assess-path exposure derivation acts on."""

    def __init__(self, *, exposure: float = 0.0, explicit: bool = False, auto: bool = True):
        self._bde = m.BenefitDeltaEvidence(
            source_type="measured", deltas={"complexity_delta": -2.0},
            future_change_exposure=exposure, future_change_exposure_explicit=explicit,
            auto_exposure_allowed=auto,
        )

    def gather_evidence(self, request, action, repo_root):
        from dataclasses import replace  # noqa: PLC0415
        base = FakeEvidence().gather_evidence(request, action, repo_root)
        return replace(base, benefit_delta_evidence=self._bde)


def _trusted_fanin(pct: float = 0.9):
    return m.FanInEvidence(
        graph_freshness="fresh", resolution_method="location", graph_file_error_count=0,
        symbol_fan_in_percentile=pct, symbol_caller_count=3,
    )


def _run_benefit(evidence_provider, fanin_ev):
    return ac.assess(
        _request(), thresholds=_THRESHOLDS, start_path="/abs/path/to/example-repo/src",
        evidence_provider=evidence_provider, symbol_diff_provider=FakeSymbolDiff(),
        blast_provider=FakeBlast(), sanction_port=FakeSanction(), repository_registry=FakeRegistry(),
        store=FakeStore(),
        fanin_provider=FakeFanInProvider(fanin_ev) if fanin_ev is not None else None,
    )


_IMMEDIATE = 0.82  # FakeEvidence.immediate_benefit — benefit == this exactly when nothing is credited


def test_derived_exposure_credits_benefit_by_default() -> None:
    # measured RCA delta + unset exposure + trusted high fan-in -> benefit credited WITHOUT the request.
    out = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0), _trusted_fanin(0.9))
    assert out.recommended_result.scores["benefit"] > _IMMEDIATE


def test_request_supplied_measured_delta_does_not_get_derived_exposure() -> None:
    # A caller can label request JSON as source_type="measured"; that must NOT receive trusted graph
    # exposure unless it came from the provider-filled RCA path (auto_exposure_allowed=True).
    out = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0, auto=False), _trusted_fanin(0.9))
    assert out.recommended_result.scores["benefit"] == _IMMEDIATE


def test_explicit_zero_caller_exposure_is_not_clobbered() -> None:
    # an EXPLICIT caller 0.0 ("credit nothing, on purpose") must survive the derivation.
    out = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0, explicit=True), _trusted_fanin(0.9))
    assert out.recommended_result.scores["benefit"] == _IMMEDIATE


def test_explicit_nonzero_caller_exposure_wins_over_derived() -> None:
    explicit = _run_benefit(_MeasuredBenefitEvidence(exposure=0.3, explicit=True), _trusted_fanin(0.9))
    derived = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0), _trusted_fanin(0.9))
    exp_scores = explicit.recommended_result.scores
    der_scores = derived.recommended_result.scores
    g_exp = exp_scores["benefit_breakdown"]["credited_maintainability_gain"]
    g_der = der_scores["benefit_breakdown"]["credited_maintainability_gain"]
    assert 0.0 < g_exp < g_der  # caller's 0.3 credited (not derived 0.9), never clobbered
    assert exp_scores["benefit"] <= der_scores["benefit"] <= 1.0


def test_absent_graph_falls_back_to_no_credit() -> None:
    out = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0), None)  # no trusted fan-in
    assert out.recommended_result.scores["benefit"] == _IMMEDIATE


def test_exposure_derivation_never_changes_risk() -> None:
    # Hold fan-in CONSTANT (identical risk effect) and toggle ONLY the derivation via the explicit
    # flag: on (explicit=False) vs off (explicit=True). Risk must be bit-identical; only benefit moves.
    fanin = _trusted_fanin(0.9)
    on = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0), fanin)
    off = _run_benefit(_MeasuredBenefitEvidence(exposure=0.0, explicit=True), fanin)
    d, o = on.recommended_result.scores, off.recommended_result.scores
    assert d["expected_loss"] == o["expected_loss"]
    assert d["loss_components"] == o["loss_components"]
    assert d["benefit"] > o["benefit"]
