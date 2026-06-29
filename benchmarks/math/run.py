"""Run the math-tier oracle validation and emit a deterministic, JSON-serializable report.

The report is the regenerable artifact: ``same fixtures + same PEBRA commit -> byte-identical JSON``.
Floats are normalized to a fixed precision so the artifact does not churn on platform float repr.

Run directly to print the report:  ``python -m benchmarks.math.run``
"""

from __future__ import annotations

import json
import sys

from benchmarks.math import oracle_metrics as om

SCHEMA_VERSION = 1
PRECISION = 12  # decimals; normalizes the report so byte-identity is platform-stable

# Default fixtures (mirrors tests/oracles): a spread of confident/uncertain binary predictions, plus a
# small continuous set for MSE. Deterministic and committed so the report is reproducible.
DEFAULT_PAIRS: list[tuple[float, int]] = [
    (0.90, 1), (0.20, 0), (0.80, 1), (0.10, 0),
    (0.55, 1), (0.45, 0), (0.99, 1), (0.01, 0), (0.70, 0),
]
DEFAULT_CONT_PAIRS: list[tuple[float, float]] = [
    (0.30, 0.25), (0.10, 0.12), (0.80, 0.77), (0.50, 0.61),
]


def _result_to_dict(r: om.OracleResult) -> dict:
    return {
        "metric": r.metric,
        "pebra": round(r.pebra, PRECISION),
        "reference": round(r.reference, PRECISION),
        "abs_diff": round(r.abs_diff, PRECISION),
        "tolerance": round(float(r.tolerance), PRECISION),
        "passed": bool(r.passed),
    }


def run_validation(
    pairs: list[tuple[float, int]] | None = None,
    cont_pairs: list[tuple[float, float]] | None = None,
    n_bins: int = 10,
) -> dict:
    """Validate every PEBRA core formula against its reference and aggregate into a report dict.

    ``passed`` is the AND over all results — uses the raw (un-rounded) tolerance check inside each
    :class:`~benchmarks.math.oracle_metrics.OracleResult`, so a sub-precision divergence still counts.
    """
    pairs = pairs if pairs is not None else DEFAULT_PAIRS
    cont_pairs = cont_pairs if cont_pairs is not None else DEFAULT_CONT_PAIRS
    results = [
        om.validate_brier(pairs),
        om.validate_ece(pairs, n_bins=n_bins),
        om.validate_log_loss(pairs),
        om.validate_mse(cont_pairs),
    ]
    results.sort(key=lambda r: r.metric)  # stable ordering for a deterministic artifact
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": all(r.passed for r in results),
        "results": [_result_to_dict(r) for r in results],
    }


def to_json(report: dict) -> str:
    """Deterministic serialization: sorted keys so the artifact is byte-identical across runs."""
    return json.dumps(report, sort_keys=True, indent=2)


def main(argv: list[str] | None = None) -> int:
    report = run_validation()
    print(to_json(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    sys.exit(main())
