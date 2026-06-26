"""CalibrationPort (Architecture §3). Protocol contract only (shadow read in Phase 3)."""

from __future__ import annotations

from typing import Any, Protocol


class CalibrationPort(Protocol):
    def calibration_data(self, repo_id: str) -> dict[str, Any]: ...

    def production_calibration_data(self, repo_id: str) -> dict[str, Any]:
        """Filtered M5 promotion input; excludes shadow/guided/censored rows."""
        ...
