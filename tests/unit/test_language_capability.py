"""Phase 0 — the pure LanguageCapability value + classify_tier ladder.

classify_tier derives a coarse support tier from MEASURED graph coverage ratios ONLY (never from a
declared/assumed language list): a language is only claimed to a tier because the indexed graph proves
it, so 'unavailable'/'risk_only' can never be mistaken for 'verified full support'.
"""

from __future__ import annotations

from pebra.core.language_capability import (
    TIER_FULL,
    TIER_PARTIAL,
    TIER_RISK_ONLY,
    TIER_UNKNOWN,
    LanguageCapability,
    classify_tier,
)


def test_default_capability_is_unmeasured_unknown() -> None:
    cap = LanguageCapability()
    assert cap.language == "unknown"
    assert cap.probe_status == "unmeasured"
    assert cap.node_count == 0
    assert cap.signature_coverage_ratio == 0.0
    assert cap.edge_kinds == frozenset()
    # an unmeasured capability must not claim any support tier
    assert classify_tier(cap) == TIER_UNKNOWN


def test_unmeasured_or_graph_unavailable_is_unknown() -> None:
    assert classify_tier(LanguageCapability(probe_status="unmeasured")) == TIER_UNKNOWN
    assert classify_tier(
        LanguageCapability(probe_status="graph_unavailable", fallback_reason="no engine")
    ) == TIER_UNKNOWN


def test_signatures_plus_visibility_is_full() -> None:
    cap = LanguageCapability(
        language="typescript", probe_status="measured", node_count=500,
        signature_coverage_ratio=0.9, visibility_coverage_ratio=0.9,
    )
    assert classify_tier(cap) == TIER_FULL


def test_visibility_without_signatures_is_partial() -> None:
    # the C# case: the graph proves exported/abstract contract surface but carries no signature text,
    # so coarse (exported + body_changed) diff is meaningful but signature-level detail is not.
    cap = LanguageCapability(
        language="csharp", probe_status="measured", node_count=13000,
        signature_coverage_ratio=0.0, visibility_coverage_ratio=0.95,
    )
    assert classify_tier(cap) == TIER_PARTIAL


def test_owners_without_visibility_is_risk_only() -> None:
    # callable nodes resolve (fan-in/risk works) but contract surface can't be classified reliably.
    cap = LanguageCapability(
        language="obscure", probe_status="measured", node_count=200,
        signature_coverage_ratio=0.0, visibility_coverage_ratio=0.1,
    )
    assert classify_tier(cap) == TIER_RISK_ONLY


def test_measured_but_no_callable_nodes_is_risk_only() -> None:
    cap = LanguageCapability(language="markup", probe_status="measured", node_count=0,
                             visibility_coverage_ratio=0.0)
    assert classify_tier(cap) == TIER_RISK_ONLY
