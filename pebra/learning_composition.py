"""Learning/scorecard composition root.

Kept separate from ``pebra.composition`` so assess surfaces can import their composition root without
any static path to learning writers or calibration readers.
"""

from __future__ import annotations

from pebra.adapters.calibration_store import CalibrationStore
from pebra.adapters.learning_store import LearningStore
from pebra.composition import RepoContext


def build_learning_port(ctx: RepoContext) -> LearningStore:
    """The shadow-measurement write port for ``pebra learn`` (Milestone 4d)."""
    return LearningStore(ctx.store)


def build_calibration_store(ctx: RepoContext) -> CalibrationStore:
    """The read-only calibration summary port for ``pebra scorecard`` (Milestone 4e)."""
    return CalibrationStore(ctx.store)
