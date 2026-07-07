"""LanguageCapabilityProvider — read-only contract for the DECLARED ∩ MEASURED capability probe.

The adapter (``adapters/codegraph_adapter.py``) reads the indexed graph and reports, per language, how
much structural signal PEBRA can actually extract (callable-node count, signature/visibility coverage,
edge kinds). The controller attaches the capability for the resolved edit's language to
``AssessmentInput`` so the engine/renderer can be honest about per-language reach — never guessing.

Fail-soft at the adapter boundary: an absent/unreadable/below-schema graph yields
``LanguageCapability(probe_status='graph_unavailable', ...)``; it never raises and never fabricates a
'measured' verdict.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.language_capability import LanguageCapability


class LanguageCapabilityProvider(Protocol):
    def capability_for(self, language: str, repo_root: str) -> LanguageCapability:
        """Measured capability for one language (graph_unavailable when the graph can't be read)."""
        ...

    def probe_capabilities(self, repo_root: str) -> dict[str, LanguageCapability]:
        """Measured capability for every indexed language ({} when the graph can't be read)."""
        ...
