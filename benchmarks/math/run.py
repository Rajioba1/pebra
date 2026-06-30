"""Run the Tauri-style math validation pipeline.

Flow:

``prediction_errors.csv -> reference_metrics.json``
``prediction_errors.csv -> pebra_metrics.json``
``reference_metrics.json + pebra_metrics.json -> comparison.json``

The comparison has no known-divergence bypass: any metric outside tolerance fails the report.
"""

from __future__ import annotations

import sys
from pathlib import Path

from benchmarks.math import compare, fixture_loader as fl, pebra_metrics, reference_metrics

DATA_FILE: Path = fl.FIXTURE_CSV


def run_validation_from_csv(csv_path: Path = DATA_FILE, *, write: bool = False) -> dict:
    """Compute both artifact lanes from the CSV and compare them."""
    reference = reference_metrics.compute_reference_from_csv(csv_path)
    pebra = pebra_metrics.compute_pebra_from_csv(csv_path)
    comparison = compare.compare_artifacts(reference, pebra)
    if write:
        reference_metrics.REFERENCE_METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
        reference_metrics.REFERENCE_METRICS_JSON.write_text(
            reference_metrics.to_json(reference), encoding="utf-8"
        )
        pebra_metrics.PEBRA_METRICS_JSON.write_text(pebra_metrics.to_json(pebra), encoding="utf-8")
        compare.COMPARISON_JSON.write_text(compare.to_json(comparison), encoding="utf-8")
    return comparison


def main(argv: list[str] | None = None) -> int:
    write = bool(argv and "--write" in argv)
    comparison = run_validation_from_csv(write=write)
    print(compare.to_json(comparison), end="")
    return 0 if comparison["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    sys.exit(main(sys.argv[1:]))
