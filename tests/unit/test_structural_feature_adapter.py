"""Phase-4 reframe — PEBRA-owned structural feature adapter (no external codeindex/sem)."""

from __future__ import annotations

from types import SimpleNamespace

from pebra.adapters.structural_feature_adapter import StructuralFeatureAdapter
from pebra.core.constants import GraphFreshness
from pebra.core.models import ArchitectureEvidence, CandidateAction, SymbolDiffEvidence


def _inp(*, arch=None, sde=None, action=None, repo_root="/x", criticality_stage="C3"):
    return SimpleNamespace(
        architecture_evidence=arch or ArchitectureEvidence(),
        symbol_diff_evidence=sde or SymbolDiffEvidence(),
        action=action or CandidateAction(id="a1", label="x", action_type="edit", expected_files=["src/payments/charge.py"]),
        repo_root=repo_root,
        criticality_stage=criticality_stage,
    )


def test_passthrough_structural_signals() -> None:
    arch = ArchitectureEvidence(
        god_node_score=0.95, bridge_centrality=0.4, cycle_participation=True,
        domain_entrypoint=True, fan_out=7, matched_domains=["payments"],
        matched_anchors=["src/payments/charge.py"], graph_freshness=GraphFreshness.REBUILT,
    )
    sde = SymbolDiffEvidence(changed_symbols=["src/payments/charge.py::charge"],
                             max_change_kind="CONTRACT", visibility="internal")
    f = StructuralFeatureAdapter().build_features(_inp(arch=arch, sde=sde))
    st = f["structural"]
    assert st["container_file_fan_in_percentile"] == 0.95
    assert st["is_high_container_fan_in"] is True
    assert st["bridge_centrality"] == 0.4
    assert st["cycle_participation"] is True
    assert st["is_architecture_anchor"] is True
    assert st["dependency_boundary"] is True          # bridge_centrality > 0
    assert f["symbol"]["signature_changed"] is True   # CONTRACT
    assert f["symbol"]["body_changed"] is False
    assert f["domain"]["matched_domains"] == ["payments"]
    assert f["provenance"]["graph_freshness"] == "rebuilt"


def test_dependency_boundary_false_without_cross_domain_edges() -> None:
    f = StructuralFeatureAdapter().build_features(_inp(arch=ArchitectureEvidence(bridge_centrality=0.0)))
    assert f["structural"]["dependency_boundary"] is False


def test_public_api_via_all_in_package_init(tmp_path) -> None:
    pkg = tmp_path / "src" / "payments"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__all__ = ['charge']\n", encoding="utf-8")
    (pkg / "charge.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    sde = SymbolDiffEvidence(changed_symbols=["src/payments/charge.py::charge"],
                             max_change_kind="BEHAVIORAL", visibility="internal")
    action = CandidateAction(id="a1", label="x", action_type="edit", expected_files=["src/payments/charge.py"])
    f = StructuralFeatureAdapter().build_features(
        _inp(sde=sde, action=action, repo_root=str(tmp_path))
    )
    assert f["symbol"]["is_public_api"] is True


def test_public_api_false_when_no_init_or_repo(tmp_path) -> None:
    sde = SymbolDiffEvidence(changed_symbols=["src/lib/util.py::helper"],
                             max_change_kind="BEHAVIORAL", visibility="internal")
    f = StructuralFeatureAdapter().build_features(_inp(sde=sde, repo_root=str(tmp_path)))
    assert f["symbol"]["is_public_api"] is False


def test_codegraph_provenance_recorded_when_present() -> None:
    from pebra.core.models import FanInEvidence

    inp = _inp()
    inp.fanin_evidence = FanInEvidence(
        symbol_fan_in_percentile=0.9, resolution_method="location",
        provider_version="1.1.1", index_version="24", graph_freshness="fresh",
    )
    p = StructuralFeatureAdapter().build_features(inp)["provenance"]
    assert p["provider_version"] == "1.1.1"
    assert p["index_version"] == "24"
    assert p["fanin_graph_freshness"] == "fresh"
    assert p["fanin_resolution_method"] == "location"


def test_no_codegraph_provenance_keys_when_absent() -> None:
    # _inp() has no fanin_evidence -> the provenance keys are simply absent (no crash)
    p = StructuralFeatureAdapter().build_features(_inp())["provenance"]
    assert "provider_version" not in p


def test_graceful_when_no_changed_symbols() -> None:
    f = StructuralFeatureAdapter().build_features(
        _inp(sde=SymbolDiffEvidence(changed_symbols=[]),
             action=CandidateAction(id="a1", label="x", action_type="edit", expected_files=[]))
    )
    assert f["schema_version"] == 1
    assert f["symbol"]["symbol_id"] == ""
    assert f["symbol"]["is_public_api"] is False
