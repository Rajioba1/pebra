"""SanctionPort (Architecture §3, AD-26). Protocol contract only.

The controller pre-fetches the active sanction (if any) and places it in AssessmentInput; the engine
never calls this port.
"""

from __future__ import annotations

from typing import Any, Protocol

from pebra.core.models import CandidateAction


class SanctionPort(Protocol):
    def active_sanction(
        self, repo_id: str, action: CandidateAction
    ) -> dict[str, Any] | None: ...

    def create_sanction(self, repo_id: str, sanction: dict[str, Any]) -> str: ...
