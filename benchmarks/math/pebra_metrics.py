"""PEBRA-lane metrics for the math benchmark.

This is the Tauri-style "Python implementation lane": it reads the committed CSV fixture, computes
metrics with PEBRA's own core functions, and writes a standalone artifact. The comparison step decides
pass/fail; this module only reports PEBRA's values.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.math import fixture_loader as fl
from benchmarks.math.reference_metrics import PRECISION, RESULTS_DIR
from pebra.core import learning_eval as le
from pebra.core.prediction_error import mean_brier, mean_log_loss, mse

SCHEMA_VERSION = 1
PEBRA_METRICS_JSON = RESULTS_DIR / "pebra_metrics.json"


def _metric(value: float) -> dict:
    return {"value": round(float(value), PRECISION)}


def compute_pebra_artifact(
    binary_pairs: list[tuple[float, int]],
    cont_pairs: list[tuple[float, float]],
    *,
    fixture_file: str = fl.FIXTURE_CSV.name,
    n_bins: int = 10,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "pebra.core",
        "fixture_file": fixture_file,
        "n_binary": len(binary_pairs),
        "n_cont": len(cont_pairs),
        "n_bins": n_bins,
        "metrics": {
            "brier": _metric(mean_brier(binary_pairs)),
            "ece": _metric(le.ece(binary_pairs, n_bins=n_bins)),
            "log_loss": _metric(mean_log_loss(binary_pairs)),
            "mse": _metric(mse(cont_pairs)),
        },
    }


def compute_pebra_from_csv(csv_path: Path = fl.FIXTURE_CSV, n_bins: int = 10) -> dict:
    binary, cont = fl.load_all_pairs(csv_path)
    return compute_pebra_artifact(binary, cont, fixture_file=Path(csv_path).name, n_bins=n_bins)


def to_json(artifact: dict) -> str:
    return json.dumps(artifact, sort_keys=True, indent=2) + "\n"


def write_pebra_artifact(
    csv_path: Path = fl.FIXTURE_CSV,
    out_path: Path = PEBRA_METRICS_JSON,
    n_bins: int = 10,
) -> dict:
    artifact = compute_pebra_from_csv(csv_path, n_bins=n_bins)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_json(artifact), encoding="utf-8")
    return artifact


def main() -> int:
    write_pebra_artifact()
    print(f"wrote {PEBRA_METRICS_JSON}")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
