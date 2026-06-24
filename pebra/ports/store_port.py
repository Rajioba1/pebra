"""StorePort (Architecture §3, §10). Protocol contract only.

Persists assessments + guidance packets in a hash-chained, append-only store and verifies the chain.
"""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import AssessmentResult


class StorePort(Protocol):
    def persist_assessment(
        self, result: AssessmentResult, request_payload: dict[str, Any]
    ) -> str:
        """Append an assessment (and its guidance packet) to the hash chain; return its id."""
        ...

    def validate_chain(self) -> bool:
        """Return True iff the stored hash chain is intact (tamper-evident)."""
        ...
