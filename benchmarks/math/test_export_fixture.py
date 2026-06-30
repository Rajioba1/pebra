"""Phase A2: structural guarantees of the offline fixture exporter.

CI does NOT run the ignition loop here (that is the `bench-math-regen` job, the Tauri "R runs offline"
analog). These tests pin the pure normalization/sorting/formatting helpers + the in-sync guard that
catches a hand-edited CSV or reference artifact. The full export pipeline is exercised by running the
module.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn.metrics")
pytest.importorskip("numpy")

from benchmarks.math import compare  # noqa: E402
from benchmarks.math import export_fixture as ef  # noqa: E402
from benchmarks.math import fixture_loader as fl  # noqa: E402
from benchmarks.math import pebra_metrics as pm  # noqa: E402
from benchmarks.math import reference_metrics as ref  # noqa: E402


def test_strip_row_keeps_only_export_columns():
    raw = {
        "target_type": "risk_binary", "target_name": "p_success",
        "predicted_probability": 0.5, "predicted_value": None,
        "actual_outcome": 1, "actual_value": None, "outcome_label_status": "observed",
        # chain / derived fields that MUST be stripped:
        "brier_error": 0.25, "log_loss": 0.69, "shadow_mode": 0, "assessment_id": "x",
        "calibration_scope": "proceeded_edits_only", "guidance_packet_id": None,
    }
    assert set(ef._strip_row(raw)) == set(ef.EXPORT_COLUMNS)


def test_sort_rows_is_deterministic_and_ordered():
    rows = [
        {"target_type": "risk_binary", "target_name": "p_success",
         "predicted_probability": 0.9, "predicted_value": None},
        {"target_type": "risk_binary", "target_name": "p_success",
         "predicted_probability": 0.1, "predicted_value": None},
    ]
    assert ef._sort_rows(rows) == ef._sort_rows(rows)
    assert ef._sort_rows(rows)[0]["predicted_probability"] == 0.1


def test_fmt_is_platform_stable():
    assert ef._fmt(None) == ""
    assert ef._fmt(1) == "1"
    assert ef._fmt(0.1) == "0.1"
    assert ef._fmt(0.55) == "0.55"


def test_module_exposes_cli_entrypoint():
    assert callable(ef.main)


def test_committed_csv_and_reference_artifact_are_in_sync():
    # The headline guard: recompute the oracle from the committed CSV and check it still matches the
    # committed reference artifact within tolerance. Catches a hand-edited CSV-or-JSON desync.
    if not ef.FIXTURE_CSV.exists() or not ref.REFERENCE_METRICS_JSON.exists():
        pytest.skip("fixture not generated yet (run: python -m benchmarks.math.export_fixture)")
    reference = ef._load_json(ref.REFERENCE_METRICS_JSON)
    binary, cont = fl.load_all_pairs(ef.FIXTURE_CSV)
    live = {
        "brier": ref.sklearn_brier(binary),
        "log_loss": ref.sklearn_log_loss(binary),
        "ece": ref.numpy_ece(binary),
        "mse": ref.sklearn_mse(cont),
    }
    for metric, frozen in reference["metrics"].items():
        assert abs(live[metric] - frozen["value"]) <= frozen["tolerance"], metric


def test_committed_pebra_artifact_matches_recomputation():
    # The PEBRA lane is pure pebra.core (deterministic, stdlib), so the committed artifact must be
    # BYTE-identical to a fresh recompute — catches a stale/hand-edited pebra_metrics.json.
    if not ef.FIXTURE_CSV.exists() or not pm.PEBRA_METRICS_JSON.exists():
        pytest.skip("pebra artifact not generated yet (run: python -m benchmarks.math.run --write)")
    live = pm.to_json(pm.compute_pebra_from_csv(ef.FIXTURE_CSV))
    assert live == pm.PEBRA_METRICS_JSON.read_text(encoding="utf-8")


def test_committed_comparison_artifact_is_consistent_with_csv():
    # comparison.json embeds the sklearn reference (cross-platform last-ULP slack), so it is checked by
    # tolerance, not byte-identity: recompute both lanes from the committed CSV and reconcile.
    if not ef.FIXTURE_CSV.exists() or not compare.COMPARISON_JSON.exists():
        pytest.skip("comparison artifact not generated yet (run: python -m benchmarks.math.run --write)")
    committed = ef._load_json(compare.COMPARISON_JSON)
    live = compare.compare_artifacts(
        ref.compute_reference_from_csv(ef.FIXTURE_CSV), pm.compute_pebra_from_csv(ef.FIXTURE_CSV)
    )
    assert committed["passed"] is True and live["passed"] is True
    live_by_metric = {r["metric"]: r for r in live["results"]}
    for row in committed["results"]:
        live_row = live_by_metric[row["metric"]]
        assert abs(row["pebra"] - live_row["pebra"]) <= 1e-12            # pebra lane is deterministic
        assert abs(row["reference"] - live_row["reference"]) <= row["tolerance"]  # sklearn slack
