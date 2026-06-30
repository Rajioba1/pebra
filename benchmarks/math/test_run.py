"""Math benchmark runner: CSV -> reference artifact + PEBRA artifact -> comparison artifact."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sklearn.metrics")
pytest.importorskip("numpy")

from benchmarks.math import compare, pebra_metrics, reference_metrics, run as run_mod  # noqa: E402


def test_reference_artifact_uses_committed_csv():
    artifact = reference_metrics.compute_reference_from_csv()
    assert artifact["schema_version"] == 1
    assert artifact["source"] == "sklearn/numpy"
    assert artifact["fixture_file"] == "prediction_errors.csv"
    assert artifact["n_binary"] >= 8
    assert artifact["n_cont"] >= 4
    assert set(artifact["metrics"]) == {"brier", "ece", "log_loss", "mse"}


def test_pebra_artifact_uses_committed_csv():
    artifact = pebra_metrics.compute_pebra_from_csv()
    assert artifact["schema_version"] == 1
    assert artifact["source"] == "pebra.core"
    assert artifact["fixture_file"] == "prediction_errors.csv"
    assert set(artifact["metrics"]) == {"brier", "ece", "log_loss", "mse"}


def test_comparison_reports_every_metric_and_overall_pass():
    report = run_mod.run_validation_from_csv()
    assert report["schema_version"] == 1
    assert report["passed"] is True
    metrics = [r["metric"] for r in report["results"]]
    assert metrics == sorted(metrics)
    assert set(metrics) == {"brier", "ece", "log_loss", "mse"}
    for row in report["results"]:
        assert {"metric", "pebra", "reference", "abs_diff", "tolerance", "passed"} <= set(row)
        assert row["passed"] is True


def test_comparison_has_no_known_divergence_or_coercion_fields():
    report = run_mod.run_validation_from_csv()
    for row in report["results"]:
        assert "known_divergence" not in row
        assert "issue" not in row


def test_comparison_json_is_byte_identical_across_runs():
    a = compare.to_json(run_mod.run_validation_from_csv())
    b = compare.to_json(run_mod.run_validation_from_csv())
    assert a == b
    assert isinstance(a, str)


def test_artifact_writer_round_trip(tmp_path):
    report = run_mod.run_validation_from_csv()
    reference_path = tmp_path / "reference_metrics.json"
    pebra_path = tmp_path / "pebra_metrics.json"
    comparison_path = tmp_path / "comparison.json"

    reference = reference_metrics.compute_reference_from_csv()
    pebra = pebra_metrics.compute_pebra_from_csv()
    reference_path.write_text(reference_metrics.to_json(reference), encoding="utf-8")
    pebra_path.write_text(pebra_metrics.to_json(pebra), encoding="utf-8")
    written = compare.write_comparison(reference_path, pebra_path, comparison_path)

    assert written == report
    assert json.loads(comparison_path.read_text(encoding="utf-8")) == report


def test_main_write_updates_all_three_artifacts(tmp_path, monkeypatch):
    ref_path = tmp_path / "reference_metrics.json"
    pebra_path = tmp_path / "pebra_metrics.json"
    cmp_path = tmp_path / "comparison.json"
    monkeypatch.setattr(reference_metrics, "REFERENCE_METRICS_JSON", ref_path)
    monkeypatch.setattr(pebra_metrics, "PEBRA_METRICS_JSON", pebra_path)
    monkeypatch.setattr(compare, "COMPARISON_JSON", cmp_path)

    report = run_mod.run_validation_from_csv(write=True)

    assert json.loads(ref_path.read_text(encoding="utf-8"))["source"] == "sklearn/numpy"
    assert json.loads(pebra_path.read_text(encoding="utf-8"))["source"] == "pebra.core"
    assert json.loads(cmp_path.read_text(encoding="utf-8")) == report
