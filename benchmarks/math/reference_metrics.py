"""Independent reference-lane metrics for the math benchmark.

This is the Tauri-style "R lane" equivalent for PEBRA's closed-form metrics: it reads the committed
CSV fixture, computes metrics with numpy/sklearn only, and writes a standalone reference artifact.
It deliberately does not import ``pebra.core``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn import metrics as sk

from benchmarks.math import fixture_loader as fl

SCHEMA_VERSION = 1
PRECISION = 12
RESULTS_DIR = Path(__file__).parent / "results"
REFERENCE_METRICS_JSON = RESULTS_DIR / "reference_metrics.json"

TOLERANCES = {"brier": 1e-10, "ece": 1e-10, "log_loss": 1e-9, "mse": 1e-10}


def numpy_ece(pairs: list[tuple[float, int]], n_bins: int = 10) -> float:
    """Equal-width ECE reference using edge-based binning."""
    p = np.array([pp for pp, _ in pairs], dtype=float)
    y = np.array([yy for _, yy in pairs], dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    n = len(pairs)
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if not mask.any():
            continue
        ece += (int(mask.sum()) / n) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def _metric(value: float, tolerance: float) -> dict:
    return {"value": round(float(value), PRECISION), "tolerance": tolerance}


def sklearn_brier(pairs: list[tuple[float, int]]) -> float:
    return float(
        sk.brier_score_loss(
            [y for _, y in pairs], [p for p, _ in pairs], scale_by_half=True
        )
    )


def sklearn_log_loss(pairs: list[tuple[float, int]]) -> float:
    return float(sk.log_loss([y for _, y in pairs], [p for p, _ in pairs], labels=[0, 1]))


def sklearn_mse(pairs: list[tuple[float, float]]) -> float:
    return float(sk.mean_squared_error([a for _, a in pairs], [p for p, _ in pairs]))


def compute_reference_artifact(
    binary_pairs: list[tuple[float, int]],
    cont_pairs: list[tuple[float, float]],
    *,
    fixture_file: str = fl.FIXTURE_CSV.name,
    n_bins: int = 10,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "sklearn/numpy",
        "fixture_file": fixture_file,
        "n_binary": len(binary_pairs),
        "n_cont": len(cont_pairs),
        "n_bins": n_bins,
        "metrics": {
            "brier": _metric(sklearn_brier(binary_pairs), TOLERANCES["brier"]),
            "ece": _metric(numpy_ece(binary_pairs, n_bins=n_bins), TOLERANCES["ece"]),
            "log_loss": _metric(sklearn_log_loss(binary_pairs), TOLERANCES["log_loss"]),
            "mse": _metric(sklearn_mse(cont_pairs), TOLERANCES["mse"]),
        },
    }


def compute_reference_from_csv(csv_path: Path = fl.FIXTURE_CSV, n_bins: int = 10) -> dict:
    binary, cont = fl.load_all_pairs(csv_path)
    return compute_reference_artifact(binary, cont, fixture_file=Path(csv_path).name, n_bins=n_bins)


def to_json(artifact: dict) -> str:
    return json.dumps(artifact, sort_keys=True, indent=2) + "\n"


def write_reference_artifact(
    csv_path: Path = fl.FIXTURE_CSV,
    out_path: Path = REFERENCE_METRICS_JSON,
    n_bins: int = 10,
) -> dict:
    artifact = compute_reference_from_csv(csv_path, n_bins=n_bins)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_json(artifact), encoding="utf-8")
    return artifact


def main() -> int:
    write_reference_artifact()
    print(f"wrote {REFERENCE_METRICS_JSON}")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
