"""language_capability (multi-language Phase 0/1) — the DECLARED ∩ MEASURED capability model.

PEBRA is language-agnostic in its risk math but its structural signal varies by language: some
languages carry full signatures in CodeGraph, some only identity+visibility (e.g. C#), some only
file-level nodes. This module holds the pure value that records what the indexed graph ACTUALLY
provides for a language (measured by an adapter probe), plus the pure tier ladder derived from it.

Two honesty rules encoded here:
  1. ``DECLARED_LANGUAGES`` is a NON-authoritative advertisement (for CLI help only). It NEVER asserts
     support — only the MEASURED probe (``probe_status == "measured"``) can place a language in a tier.
  2. ``classify_tier`` derives the tier from measured coverage ratios ONLY, so an unmeasured or
     graph-unavailable language falls to ``unknown`` rather than being optimistically claimed.

Pure: stdlib only. No adapter/graph/IO here — the probe lives in codegraph_adapter behind a port.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Support tiers (coarse, measured-only). See classify_tier for the exact ladder.
TIER_FULL = "full"            # signatures + visibility present -> fine-grained diff + contract surface
TIER_PARTIAL = "partial"      # visibility present, no signatures -> coarse (exported + body) diff only
TIER_RISK_ONLY = "risk_only"  # callable owners weak/absent -> risk (fan-in/blast) only, no diff tier
TIER_UNKNOWN = "unknown"      # not measured (or graph unavailable) -> claim nothing

# A field is considered "covered" for a language when at least this fraction of its callable nodes
# populate it. Majority-based and deliberately simple; tunable once real multi-language data exists.
_SIGNATURE_COVERAGE_FLOOR = 0.5
_VISIBILITY_COVERAGE_FLOOR = 0.5

# Non-authoritative advertisement of languages CodeGraph is built to index. NEVER used to assert
# support (only the measured probe does that); surfaced in `pebra capabilities` help text.
DECLARED_LANGUAGES: tuple[str, ...] = (
    "python", "typescript", "javascript", "tsx", "jsx", "csharp", "java", "kotlin", "go", "rust",
    "ruby", "php", "c", "cpp", "swift", "scala",
)


@dataclass(frozen=True)
class LanguageCapability:
    """What the indexed CodeGraph actually provides for one language, measured (not assumed)."""

    language: str = "unknown"
    probe_status: str = "unmeasured"  # "measured" | "unmeasured" | "graph_unavailable"
    node_count: int = 0               # callable-kind nodes for this language
    signature_coverage_ratio: float = 0.0   # fraction of callable nodes with a non-empty signature
    visibility_coverage_ratio: float = 0.0  # fraction of callable nodes with a non-empty visibility
    edge_kinds: frozenset[str] = field(default_factory=frozenset)  # edge kinds sourced from this lang
    fallback_reason: str | None = None      # why probe_status != "measured", when applicable


def classify_tier(cap: LanguageCapability) -> str:
    """Coarse support tier from MEASURED coverage only. Fails closed to ``unknown`` when unmeasured."""
    if cap.probe_status != "measured":
        return TIER_UNKNOWN
    if cap.node_count <= 0:
        return TIER_RISK_ONLY
    has_visibility = cap.visibility_coverage_ratio >= _VISIBILITY_COVERAGE_FLOOR
    has_signatures = cap.signature_coverage_ratio >= _SIGNATURE_COVERAGE_FLOOR
    if has_signatures and has_visibility:
        return TIER_FULL
    if has_visibility:
        return TIER_PARTIAL
    return TIER_RISK_ONLY
