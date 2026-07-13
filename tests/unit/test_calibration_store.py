from __future__ import annotations

from pebra.adapters.calibration_store import CalibrationStore


class _Store:
    def __init__(self) -> None:
        self.targets: list[str] = []

    def load_prediction_errors(self, _repo_id):
        return []

    def load_production_calibration_rows(self, _repo_id, target_type):
        self.targets.append(target_type)
        return []


def test_production_summary_reads_every_independent_calibration_lane() -> None:
    store = _Store()
    summary = CalibrationStore(store).production_calibration_data("r")

    assert store.targets == [
        "risk_binary", "benefit_binary", "benefit_continuous", "cost_continuous",
    ]
    assert "cost_continuous" in summary
