"""Load the math tier's (predicted, actual) pairs from a real persisted CSV fixture.

The CSV is exported once (offline) by ``export_fixture.py`` from a real ignition-loop run and committed,
so the validation input is decoupled from the validation code (the Tauri ``data/*.csv`` analog). This
loader is pure stdlib parsing — no numpy/sklearn, no pebra imports — so it stays cheap in the
``bench-math`` session and has no dependency on the engine it feeds.

Columns: ``target_type,target_name,predicted_probability,predicted_value,actual_outcome,actual_value,
outcome_label_status``. Only ``observed`` rows enter the pairs (censored rows have no real label).
"""

from __future__ import annotations

import csv
from pathlib import Path

FIXTURE_CSV: Path = Path(__file__).parent / "data" / "prediction_errors.csv"

_BINARY_TYPES = frozenset({"risk_binary", "benefit_binary"})
_CONTINUOUS_TYPE = "benefit_continuous"


def _observed(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["outcome_label_status"] == "observed"]


def _read(csv_path: Path) -> list[dict[str, str]]:
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_binary_pairs(csv_path: Path = FIXTURE_CSV) -> list[tuple[float, int]]:
    """Observed binary (risk_binary + benefit_binary) rows as ``(predicted_probability, actual)``,
    sorted by ``(target_name, predicted_probability)`` for a stable order."""
    rows = [
        r for r in _observed(_read(csv_path)) if r["target_type"] in _BINARY_TYPES
    ]
    rows.sort(key=lambda r: (r["target_name"], float(r["predicted_probability"])))
    return [(float(r["predicted_probability"]), int(float(r["actual_outcome"]))) for r in rows]


def load_cont_pairs(csv_path: Path = FIXTURE_CSV) -> list[tuple[float, float]]:
    """Observed benefit_continuous rows as ``(predicted_value, actual_value)``, sorted by
    ``(target_name, predicted_value)`` for a stable order."""
    rows = [
        r for r in _observed(_read(csv_path)) if r["target_type"] == _CONTINUOUS_TYPE
    ]
    rows.sort(key=lambda r: (r["target_name"], float(r["predicted_value"])))
    return [(float(r["predicted_value"]), float(r["actual_value"])) for r in rows]


def load_all_pairs(
    csv_path: Path = FIXTURE_CSV,
) -> tuple[list[tuple[float, int]], list[tuple[float, float]]]:
    """Convenience: ``(binary_pairs, continuous_pairs)`` from one CSV read each."""
    return load_binary_pairs(csv_path), load_cont_pairs(csv_path)
