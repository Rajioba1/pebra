"""Phase 0 — the pure LanguageCapability value + classify_tier ladder.

classify_tier derives a coarse support tier from MEASURED graph coverage ratios ONLY (never from a
declared/assumed language list): a language is only claimed to a tier because the indexed graph proves
it, so 'unavailable'/'risk_only' can never be mistaken for 'verified full support'.
"""

from __future__ import annotations

from pebra.core.language_capability import (
    DECLARED_LANGUAGES,
    EXPORT_AS_VISIBILITY_LANGUAGES,
    TIER_FULL,
    TIER_PARTIAL,
    TIER_RISK_ONLY,
    TIER_UNKNOWN,
    LanguageCapability,
    classify_tier,
    derive_visibility_from_export,
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


def test_declared_languages_match_codegraph_extractor_map_but_do_not_assert_support() -> None:
    # This is only CLI/help vocabulary, not a support claim. The measured probe still decides tiers.
    assert DECLARED_LANGUAGES == (
        "c",
        "cpp",
        "csharp",
        "dart",
        "go",
        "java",
        "javascript",
        "jsx",
        "kotlin",
        "lua",
        "luau",
        "objc",
        "pascal",
        "php",
        "python",
        "r",
        "ruby",
        "rust",
        "scala",
        "swift",
        "tsx",
        "typescript",
    )
    assert classify_tier(LanguageCapability(language="dart")) == TIER_UNKNOWN


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


# --- is_exported -> visibility fill (Go/JS/JSX lever) --------------------------------------------


def test_export_as_visibility_languages_is_the_curated_source_verified_set() -> None:
    # Languages whose extractor populates a per-symbol is_exported and whose access model IS module
    # export. TS is included: its getVisibility is only emitted for explicit modifiers (rare in real
    # code), and is_exported is empirically per-symbol (verified varying on a real TS index), so filling
    # visibility from it is honest. tsx is deliberately NOT here yet — no real .tsx index verified.
    assert EXPORT_AS_VISIBILITY_LANGUAGES == frozenset({"go", "javascript", "jsx", "typescript"})


def test_derive_visibility_never_overrides_a_real_emitted_value() -> None:
    # A real visibility always wins, even for an allowlisted language and even if is_exported disagrees.
    assert derive_visibility_from_export("go", "public", 0) == "public"
    assert derive_visibility_from_export("typescript", "private", 1) == "private"


def test_derive_visibility_fills_only_allowlisted_languages() -> None:
    assert derive_visibility_from_export("go", None, 1) == "exported"
    assert derive_visibility_from_export("go", None, 0) == "unexported"
    assert derive_visibility_from_export("javascript", "", 1) == "exported"   # empty counts as missing
    assert derive_visibility_from_export("jsx", None, 0) == "unexported"
    # TypeScript now derives too (its extractor rarely emits getVisibility on real code, but does
    # populate a real per-symbol is_exported); an explicit modifier still wins (tested above).
    assert derive_visibility_from_export("typescript", None, 1) == "exported"
    assert derive_visibility_from_export("typescript", None, 0) == "unexported"
    # NOT allowlisted -> never fill (tsx unverified; the rest have no per-symbol is_exported).
    for lang in ("tsx", "pascal", "luau", "java", "python", "unknown", None):
        assert derive_visibility_from_export(lang, None, 1) is None


def test_derive_visibility_none_is_exported_yields_none() -> None:
    # is_exported absent (defensive) -> no fabricated value.
    assert derive_visibility_from_export("go", None, None) is None
