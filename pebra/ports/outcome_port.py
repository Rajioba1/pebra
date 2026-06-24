"""OutcomePort (Architecture §3, AD-4). Protocol contract only.

Terminal action_status (completed/skipped/rejected) is written ONLY here (Phase 3 write path).
"""

from __future__ import annotations

from typing import Protocol


class OutcomePort(Protocol):
    def record_outcome(self, assessment_id: str, status: str, detail: dict | None = None) -> None: ...
