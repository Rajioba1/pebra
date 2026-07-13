"""Phase 3c — composition root: the single wiring point shared by the CLI and MCP surfaces.

These are pure-ish wiring assertions (no git needed): RepositoryRegistry.resolve and the assess
adapters work in a plain temp dir, exactly as the worked-example golden runs in an empty cwd.
"""

from __future__ import annotations

from pebra import composition
from pebra.core import candidate_parser


def test_resolve_defaults_db_under_dot_pebra(tmp_path) -> None:
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        assert ctx.db_path.endswith("pebra.db")
        assert ".pebra" in ctx.db_path
        assert ctx.repo.repo_root
    finally:
        ctx.store.close()


def test_probe_language_capabilities_empty_without_graph(tmp_path) -> None:
    # honest empty (never a fabricated capability row) when no CodeGraph index is present
    assert composition.probe_language_capabilities(str(tmp_path)) == []


def test_resolve_honors_explicit_db(tmp_path) -> None:
    db = str(tmp_path / "custom.db")
    ctx = composition.resolve_repo_and_db(str(tmp_path), db)
    try:
        assert ctx.db_path == db
    finally:
        ctx.store.close()


def test_build_assess_ports_has_the_controller_keys(tmp_path) -> None:
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        ports = composition.build_assess_ports(req, ctx)
    finally:
        ctx.store.close()
    assert set(ports) >= {
        "evidence_provider", "symbol_diff_provider", "blast_provider",
        "sanction_port", "repository_registry", "store", "assessed_commit",
        "fanin_provider", "language_capability_provider", "materialized_diff_provider",
        "graph_risk_refinement_provider",
    }


def test_build_assess_ports_semantic_provider_is_wired_but_dark(tmp_path) -> None:
    # P3: the materialized-diff provider is wired (armed) but the dispatch only calls it when the
    # default-OFF codegraph_semantic_diff_enabled threshold is set -> dark by default.
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        provider = composition.build_assess_ports(req, ctx)["materialized_diff_provider"]
    finally:
        ctx.store.close()
    assert hasattr(provider, "diff_for_patch")


def test_build_assess_ports_wires_revision_graph_refinement_provider(tmp_path) -> None:
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        provider = composition.build_assess_ports(req, ctx)["graph_risk_refinement_provider"]
    finally:
        ctx.store.close()
    assert hasattr(provider, "analyze")


def test_build_verify_ports_has_the_controller_keys() -> None:
    ports = composition.build_verify_ports()
    assert set(ports) == {"change_verifier", "contract_surface"}
    # the verifier is wired with the semantic reproduction hook (dark behind the same threshold)
    assert ports["change_verifier"]._materialized_diff_fn is not None


def test_verify_payload_exposes_measured_benefit_and_deltas() -> None:
    # The RCA post-edit benefit is surfaced on the verify JSON boundary (not dashboard-only): both the
    # scalar measured_benefit and the raw measured_benefit_deltas must be in verify_payload's output.
    from pebra.app.verify_controller import VerifyOutcome
    from pebra.core.constants import Decision
    from pebra.core.post_assessment_guardrails import GuardrailResult

    result = GuardrailResult(
        evidence_freshness="fresh", assessed_commit="a", current_head="a",
        scope_drift_detected=False, unexpected_files=[], pre_edit_symbol_diff_summary="",
        actual_symbol_diff_summary="", symbol_change_mismatch=False, contract_surface_changes=[],
        dry_run_required=False, classification_failed=False, pre_commit_decision=Decision.PROCEED,
    )
    outcome = VerifyOutcome(
        result=result, guardrails_id="g1", repo_id="r", measured_benefit=0.42,
        measured_benefit_deltas={"complexity_delta": -2.0, "maintainability_index_delta": 5.5},
    )
    payload = composition.verify_payload(outcome)
    assert payload["measured_benefit"] == 0.42
    assert payload["measured_benefit_deltas"] == {
        "complexity_delta": -2.0, "maintainability_index_delta": 5.5,
    }
    assert payload["pre_commit_decision"] == "proceed"  # existing contract preserved


def test_verify_payload_defaults_are_empty_when_nothing_measured() -> None:
    from pebra.app.verify_controller import VerifyOutcome
    from pebra.core.constants import Decision
    from pebra.core.post_assessment_guardrails import GuardrailResult

    result = GuardrailResult(
        evidence_freshness="fresh", assessed_commit="a", current_head="a",
        scope_drift_detected=False, unexpected_files=[], pre_edit_symbol_diff_summary="",
        actual_symbol_diff_summary="", symbol_change_mismatch=False, contract_surface_changes=[],
        dry_run_required=False, classification_failed=False, pre_commit_decision=Decision.PROCEED,
    )
    payload = composition.verify_payload(VerifyOutcome(result=result, guardrails_id="g", repo_id="r"))
    assert payload["measured_benefit"] == 0.0
    assert payload["measured_benefit_deltas"] == {}  # nothing measured -> empty, distinct from a 0.0 delta
