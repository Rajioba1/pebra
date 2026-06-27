"""StructuralFeatureProvider (Phase-4 reframe / M5-prep). Protocol contract only.

Produces the structural feature payload for an edit (PEBRA-owned; no external codeindex/sem). The
controller calls it pre-scoring and attaches the result to AssessmentInput for CAPTURE only — the
engine ignores it. M5 apply_snapshot consumes the persisted payload to scope learned facts.
"""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import AssessmentInput


class StructuralFeatureProvider(Protocol):
    def build_features(self, inp: AssessmentInput) -> dict[str, Any]:
        """Return the versioned structural feature payload for the edit in ``inp``."""
        ...
