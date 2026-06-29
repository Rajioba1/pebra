"""Phase 2 (math tier): the validation RUN aggregates every oracle result into a deterministic,
JSON-serializable report — the regenerable artifact that distinguishes this tier from tests/oracles."""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn.metrics")
pytest.importorskip("numpy")

from benchmarks.math import run as run_mod  # noqa: E402


def test_run_validation_reports_every_metric_and_overall_pass():
    report = run_mod.run_validation()
    assert report["schema_version"] == 1
    assert report["passed"] is True
    metrics = [r["metric"] for r in report["results"]]
    assert metrics == sorted(metrics)  # stable ordering
    assert set(metrics) == {"brier", "ece", "log_loss", "mse"}
    for r in report["results"]:
        assert set(r) == {"metric", "pebra", "reference", "abs_diff", "tolerance", "passed"}
        assert r["passed"] is True


def test_report_is_json_byte_identical_across_runs():
    a = run_mod.to_json(run_mod.run_validation())
    b = run_mod.to_json(run_mod.run_validation())
    assert a == b  # determinism target: same inputs -> byte-identical report
    assert isinstance(a, str)


def test_report_floats_are_normalized_to_fixed_precision():
    report = run_mod.run_validation()
    for r in report["results"]:
        for k in ("pebra", "reference", "abs_diff", "tolerance"):
            v = r[k]
            assert isinstance(v, float)
            # normalized: no value carries more than the fixed precision PEBRA rounds to
            assert round(v, run_mod.PRECISION) == v
