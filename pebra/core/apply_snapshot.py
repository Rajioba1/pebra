"""apply_snapshot (M5 seam) — pure snapshot reapplication contract.

M4 hardening defines the read-path seam without enabling learning. ``snapshot=None`` is a strict
identity function. M5 fills in deterministic fact matching and provenance while preserving this
contract for cold-start and shadow-only runs.
"""

from __future__ import annotations

from typing import Any

from pebra.core.models import AssessmentInput


def apply_snapshot(inp: AssessmentInput, snapshot: Any | None = None) -> AssessmentInput:
    """Return ``inp`` unchanged when no active snapshot is supplied."""
    if snapshot is None:
        return inp
    raise NotImplementedError("active snapshot reapplication is Milestone 5")
