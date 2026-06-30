"""Compare independent reference and PEBRA metric artifacts.

No coercion, no known-divergence bypass: every metric in the reference artifact must be present in
the PEBRA artifact and must be within the reference tolerance.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.math.pebra_metrics import PEBRA_METRICS_JSON
from benchmarks.math.reference_metrics import PRECISION, REFERENCE_METRICS_JSON, RESULTS_DIR

SCHEMA_VERSION = 1
COMPARISON_JSON = RESULTS_DIR / "comparison.json"


def compare_artifacts(reference: dict, pebra: dict) -> dict:
    rows = []
    for metric in sorted(reference["metrics"]):
        ref_metric = reference["metrics"][metric]
        pebra_metric = pebra["metrics"][metric]
        ref_value = float(ref_metric["value"])
        pebra_value = float(pebra_metric["value"])
        abs_diff = abs(pebra_value - ref_value)
        tolerance = float(ref_metric["tolerance"])
        rows.append({
            "metric": metric,
            "pebra": round(pebra_value, PRECISION),
            "reference": round(ref_value, PRECISION),
            "abs_diff": round(abs_diff, PRECISION),
            "tolerance": tolerance,
            "passed": abs_diff <= tolerance,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_file": reference["fixture_file"],
        "reference_source": reference["source"],
        "pebra_source": pebra["source"],
        "n_binary": reference["n_binary"],
        "n_cont": reference["n_cont"],
        "n_bins": reference["n_bins"],
        "passed": all(r["passed"] for r in rows),
        "results": rows,
    }


def to_json(artifact: dict) -> str:
    return json.dumps(artifact, sort_keys=True, indent=2) + "\n"


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_comparison(
    reference_path: Path = REFERENCE_METRICS_JSON,
    pebra_path: Path = PEBRA_METRICS_JSON,
    out_path: Path = COMPARISON_JSON,
) -> dict:
    artifact = compare_artifacts(load_json(reference_path), load_json(pebra_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_json(artifact), encoding="utf-8")
    return artifact


def main() -> int:
    artifact = write_comparison()
    print(to_json(artifact), end="")
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
