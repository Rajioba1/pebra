"""calibration_store (Milestone 4e) — CalibrationPort impl: read-only calibration summary.

Shadow-read: aggregates the computed (shadow) prediction-error rows into the per-target-type
calibration summary the scorecard renders. Reads only; writes nothing. Aggregation math is pure
(``core/prediction_error.summarize_errors``); this adapter just supplies the rows.
"""

from __future__ import annotations

from typing import Any

from pebra.adapters.store.db import SqliteStore
from pebra.core import prediction_error
from pebra.ports.calibration_port import CalibrationPort


class CalibrationStore(CalibrationPort):
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def calibration_data(self, repo_id: str) -> dict[str, Any]:
        rows = self._store.load_prediction_errors(repo_id)
        return prediction_error.summarize_errors(rows)

    def production_calibration_data(self, repo_id: str) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for target_type in (
            "risk_binary", "benefit_binary", "benefit_continuous", "cost_continuous",
        ):
            rows.extend(self._store.load_production_calibration_rows(repo_id, target_type))
        return prediction_error.summarize_errors(rows)
