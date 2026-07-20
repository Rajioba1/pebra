"""Phase 3c — composition root: the single wiring point shared by the CLI and MCP surfaces.

These are pure-ish wiring assertions (no git needed): RepositoryRegistry.resolve and the assess
adapters work in a plain temp dir, exactly as the worked-example golden runs in an empty cwd.
"""

from __future__ import annotations

from pebra import composition
from pebra.core import candidate_parser
from pebra.core.graph_snapshot import GraphSnapshot


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


def test_graph_refinement_feature_flag_off_removes_provider(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PEBRA_GRAPH_REFINEMENT", "0")
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        provider = composition.build_assess_ports(req, ctx)["graph_risk_refinement_provider"]
    finally:
        ctx.store.close()
    assert provider is None


def test_build_verify_ports_has_the_controller_keys() -> None:
    ports = composition.build_verify_ports()
    assert set(ports) == {"change_verifier", "contract_surface"}
    # the verifier is wired with the semantic reproduction hook (dark behind the same threshold)
    assert ports["change_verifier"]._materialized_diff_fn is not None


class _PreparedGraph:
    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.snapshot = snapshot
        self.prepare_calls: list[str] = []
        self.bind_calls: list[tuple[str, str | None]] = []

    def prepare(self, repo_root: str) -> GraphSnapshot:
        self.prepare_calls.append(repo_root)
        return self.snapshot

    def bind_assessed_commit(self, repo_root: str, assessed_commit: str | None) -> bool:
        self.bind_calls.append((repo_root, assessed_commit))
        return assessed_commit == self.snapshot.repo_head

    def direct_caller_files_result(self, *_args):
        return {"available": False}

    def percentiles_by_name(self, *_args):
        return {}

    def structural_symbols(self, *_args):
        return None

    def capability_for(self, *_args):
        return None

    def node_counts(self, *_args):
        return {"total": 0, "callable": 0, "csharp_callable": 0}

    def probe_capabilities(self, *_args):
        return {}

    def dependent_files(self, *_args):
        return []

    def dependent_files_result(self, *_args):
        return {"available": False, "dependent_files": []}


def _snapshot(head: str) -> GraphSnapshot:
    return GraphSnapshot(
        status="available", provider="CodeGraph", provider_version="1.1.1",
        index_version="24", repo_head=head, config_digest="config",
        graph_scope_digest="scope", sync_performed=True, fallback_reason=None,
    )


def test_build_assess_ports_prepares_once_then_reads_assessed_commit_independently(
    monkeypatch, tmp_path
) -> None:
    graph = _PreparedGraph(_snapshot("b"))
    monkeypatch.setattr(composition, "CodeGraphAdapter", lambda: graph)
    monkeypatch.setattr(composition.git_adapter, "head_commit", lambda _root: "b")
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        ports = composition.build_assess_ports(req, ctx)
    finally:
        ctx.store.close()

    assert graph.prepare_calls == [ctx.repo.repo_root]
    assert graph.bind_calls == [(ctx.repo.repo_root, "b")]
    assert ports["graph_snapshot"] is graph.snapshot
    assert ports["assessed_commit"] == "b"
    assert ports["fanin_provider"] is graph
    assert ports["file_fanin_provider"] is graph
    assert ports["language_capability_provider"] is graph


def test_build_assess_ports_rejects_snapshot_when_independent_assessed_commit_differs(
    monkeypatch, tmp_path
) -> None:
    graph = _PreparedGraph(_snapshot("b"))
    monkeypatch.setattr(composition, "CodeGraphAdapter", lambda: graph)
    monkeypatch.setattr(composition.git_adapter, "head_commit", lambda _root: "c")
    req = candidate_parser.parse({"task": "t", "candidate_actions": [{"id": "a1"}]})
    ctx = composition.resolve_repo_and_db(str(tmp_path))
    try:
        composition.build_assess_ports(req, ctx)
    finally:
        ctx.store.close()

    assert graph.bind_calls == [(ctx.repo.repo_root, "c")]


def test_build_verify_ports_prepares_once_when_repo_root_is_explicit(monkeypatch) -> None:
    graph = _PreparedGraph(_snapshot("b"))
    monkeypatch.setattr(composition, "CodeGraphAdapter", lambda: graph)

    composition.build_verify_ports("/repo")

    assert graph.prepare_calls == ["/repo"]


def test_graph_stats_capabilities_and_dependents_each_prepare_one_adapter(monkeypatch) -> None:
    graphs: list[_PreparedGraph] = []

    def factory() -> _PreparedGraph:
        graph = _PreparedGraph(_snapshot("b"))
        graphs.append(graph)
        return graph

    monkeypatch.setattr(composition, "CodeGraphAdapter", factory)

    composition.graph_node_counts("/repo")
    composition.probe_language_capabilities("/repo")
    composition.dependent_files_result("/repo", "src/a.py")

    assert len(graphs) == 3
    assert [graph.prepare_calls for graph in graphs] == [["/repo"], ["/repo"], ["/repo"]]


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
