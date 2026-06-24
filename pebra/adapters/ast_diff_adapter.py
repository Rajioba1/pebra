"""ast_diff_adapter (Phase-0 SymbolDiffProvider, AD-27) — owns symbol-diff I/O.

Phase 0: seeded with the request's ``symbol_diff`` evidence slice (a plain dict), it returns that
directly; otherwise it falls back conservatively to a cold-start ``UNKNOWN`` summary (file/path-level
risk). It deliberately does NOT hold the whole AssessmentRequest — only its own evidence slice, like
any adapter config. Real AST parsing of ``action.proposed_patch`` + per-symbol fan-in lookup arrives
in Phase 2; until then ``fallback_reason`` records why we are not at symbol granularity.
"""

from __future__ import annotations

from typing import Any

from pebra.core.models import CandidateAction, SymbolDiffEvidence

_ALLOWED = set(SymbolDiffEvidence.__dataclass_fields__)


class AstDiffAdapter:
    def __init__(self, symbol_diff_evidence: dict[str, Any] | None = None) -> None:
        self._evidence = symbol_diff_evidence

    def symbol_diff(self, action: CandidateAction, repo_root: str) -> SymbolDiffEvidence:
        if self._evidence:
            return SymbolDiffEvidence(
                **{k: v for k, v in self._evidence.items() if k in _ALLOWED}
            )
        return SymbolDiffEvidence(
            parsed_patch_available=False,
            changed_symbols=list(action.affected_symbols),
            max_change_kind="UNKNOWN",
            fallback_reason="no symbol diff supplied; Phase-0 cold-start file/path-level risk",
        )
