"""FanInProvider (M5c.5) — read-only contract for language-agnostic per-symbol fan-in.

The assess path resolves the changed symbol by LOCATION (file + old-side changed line range, taken
from the proposed patch) through codegraph's pre-built call graph, then reads the reverse-edge fan-in.
This port is the seam: the controller builds a CandidateAction, the adapter
(``adapters/codegraph_adapter.py``) reads codegraph's SQLite + shells out to the ``codegraph`` CLI for
the freshness gate, and returns a pure ``FanInEvidence`` for the engine to consume.

Strictly READ-ONLY and fail-soft at the ADAPTER boundary: when codegraph is absent, the DB is missing,
or the index is stale, the adapter returns ``FanInEvidence(resolution_method='unresolved'|
'name_fallback_ambiguous', ...)`` with a ``fallback_reason`` — it never raises and never fabricates
fan-in (percentile stays 0.0).

CONTRACT FOR THE CONSUMER (when the product decision is graph-engine-required / fail-CLEAR): a 0.0
percentile from an ``unresolved`` / ``stale`` / ``name_fallback_ambiguous`` result is the ABSENCE of
evidence, not "low fan-in = low risk". The decision engine's evidence-validity gate routes those
states to ``inspect_first`` when ``require_graph`` is enabled. Only ``resolution_method in
{'location', 'name_fallback'}`` against a ``fresh`` graph yields trusted fan-in.
"""

from __future__ import annotations

from typing import Protocol

from pebra.core.models import CandidateAction, FanInEvidence


class FanInProvider(Protocol):
    def fanin(self, action: CandidateAction, repo_root: str) -> FanInEvidence:
        """Resolve the action's changed symbol(s) by location (name fallback only) and return the
        per-symbol fan-in evidence.

        Fail-closed on a stale index: if ``codegraph status`` reports pending changes or recommends a
        reindex, returns evidence with ``graph_freshness='stale'`` and ``percentile=0.0`` (fan-in is
        not trusted against a stale graph). Returns ``resolution_method='unresolved'`` with a
        ``fallback_reason`` when codegraph/the DB is absent or the symbol cannot be located.
        """
        ...
