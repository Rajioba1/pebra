"""Phase 2 (Slice 1) — IR model additions for evidence enrichment. No behavior change.

ArchitectureEvidence (AD-22), BlastEvidence edge-confidence detail (AD-12), and the
architecture_evidence field threaded onto EvidenceBundle + AssessmentInput.
"""

from __future__ import annotations

from pebra.core import models as m
from pebra.core.constants import GraphFreshness


def test_architecture_evidence_defaults_to_unknown_freshness() -> None:
    ae = m.ArchitectureEvidence()
    assert ae.graph_freshness is GraphFreshness.UNKNOWN
    assert ae.matched_anchors == []
    assert ae.god_node_score == 0.0
    assert ae.domain_criticality_hint is None


def test_architecture_evidence_supports_rebuilt_state() -> None:
    # the user's design adds 'rebuilt' (stale -> adapter repaired -> trustworthy)
    ae = m.ArchitectureEvidence(graph_freshness=GraphFreshness.REBUILT, graph_commit="abc123")
    assert ae.graph_freshness is GraphFreshness.REBUILT
    assert ae.graph_commit == "abc123"


def test_graph_freshness_has_the_four_canonical_states() -> None:
    assert {f.value for f in GraphFreshness} == {"fresh", "rebuilt", "stale", "unknown"}


def test_blast_evidence_gains_edge_confidence_detail() -> None:
    b = m.BlastEvidence()
    assert b.edge_confidence_min == 0.0
    assert b.low_confidence_edge_count == 0
    # existing fields unchanged
    assert b.edge_confidence_mean == 0.0
    assert b.import_cycle_detected is False


def test_evidence_bundle_carries_architecture_evidence() -> None:
    bundle = m.EvidenceBundle(
        events=[], p_success=0.5, immediate_benefit=0.0, review_cost=0.2,
        criticality_stage="C2", criticality_value=0.5, edit_confidence_factors={},
    )
    assert isinstance(bundle.architecture_evidence, m.ArchitectureEvidence)
    assert bundle.architecture_evidence.graph_freshness is GraphFreshness.UNKNOWN


def test_assessment_input_carries_architecture_evidence_default() -> None:
    req = m.AssessmentRequest.single_action(task="t", action_id="a1", label="l", action_type="edit")
    inp = m.AssessmentInput(
        request=req, action=req.candidate_actions[0], events=[], p_success=0.5,
        immediate_benefit=0.0, review_cost=0.2, criticality_stage="C2", criticality_value=0.5,
        edit_confidence_factors={}, thresholds={}, repo_id="r", repo_root="/p",
    )
    assert isinstance(inp.architecture_evidence, m.ArchitectureEvidence)
