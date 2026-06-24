"""ast_import_graph (Phase-0 BlastRadiusProvider) — default blast estimate (codeindex ``d+0.5·t``).

Phase 0: seeded with the request's ``blast`` evidence slice (a plain dict), it returns that; else a
cold-start (zero-reach) estimate. It holds only its evidence slice, not the AssessmentRequest. The
real stdlib AST import-graph walk (depth buckets, edge confidence, entrypoint/cycle signals) is a
Phase-2 enrichment; nothing in the Phase-0 worked-example math depends on blast yet.
"""

from __future__ import annotations

from typing import Any

from pebra.core.models import BlastEvidence, CandidateAction

_ALLOWED = set(BlastEvidence.__dataclass_fields__)


class AstImportGraphAdapter:
    def __init__(self, blast_evidence: dict[str, Any] | None = None) -> None:
        self._evidence = blast_evidence

    def blast(self, action: CandidateAction, repo_root: str) -> BlastEvidence:
        if self._evidence:
            return BlastEvidence(**{k: v for k, v in self._evidence.items() if k in _ALLOWED})
        return BlastEvidence()
