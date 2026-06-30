"""Phase A1: the math tier loads its (predicted, actual) pairs from a REAL persisted CSV (decoupled
from code), filters to observed rows, separates binary vs continuous targets, and returns a stable
order. Pure stdlib parsing — no numpy/sklearn/pebra needed here."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.math import fixture_loader as fl

_HEADER = (
    "target_type,target_name,predicted_probability,predicted_value,"
    "actual_outcome,actual_value,outcome_label_status"
)


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join([_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def test_loader_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        fl.load_binary_pairs(tmp_path / "nope.csv")


def test_load_binary_pairs_returns_only_observed_rows(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "risk_binary,p_event.x,0.9,,1,,observed",
        "risk_binary,p_event.x,0.2,,0,,censored",   # excluded: not observed
        "risk_binary,p_success,0.7,,1,,observed",
    ])
    pairs = fl.load_binary_pairs(csv)
    # censored row dropped; sorted by (target_name, p): "p_event.x" < "p_success"
    assert pairs == [(0.9, 1), (0.7, 1)]


def test_load_binary_pairs_casts_types_correctly(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", ["risk_binary,p_success,0.55,,1,,observed"])
    (p, y), = fl.load_binary_pairs(csv)
    assert isinstance(p, float) and isinstance(y, int)
    assert p == 0.55 and y == 1


def test_load_binary_pairs_excludes_continuous_rows(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "risk_binary,p_success,0.6,,1,,observed",
        "benefit_continuous,measured_benefit,,0.3,,0.25,observed",  # not a binary target
    ])
    assert fl.load_binary_pairs(csv) == [(0.6, 1)]


def test_load_binary_pairs_includes_benefit_binary(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "risk_binary,p_success,0.6,,1,,observed",
        "benefit_binary,benefit_realized,0.8,,1,,observed",
    ])
    assert fl.load_binary_pairs(csv) == [(0.8, 1), (0.6, 1)]  # sorted by target_name


def test_load_cont_pairs_returns_only_observed_continuous(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "benefit_continuous,measured_benefit,,0.30,,0.25,observed",
        "benefit_continuous,measured_benefit,,0.10,,0.12,censored",  # excluded
        "risk_binary,p_success,0.6,,1,,observed",                     # excluded: binary
    ])
    assert fl.load_cont_pairs(csv) == [(0.30, 0.25)]


def test_load_binary_pairs_sort_order_is_stable(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "risk_binary,p_success,0.9,,1,,observed",
        "risk_binary,p_success,0.1,,0,,observed",
        "risk_binary,p_event.x,0.5,,1,,observed",
    ])
    assert fl.load_binary_pairs(csv) == fl.load_binary_pairs(csv)
    assert fl.load_binary_pairs(csv) == [(0.5, 1), (0.1, 0), (0.9, 1)]


def test_committed_fixture_csv_loads_and_is_rich_enough():
    # the real exported fixture must load and carry enough observed rows for meaningful metrics.
    binary, cont = fl.load_all_pairs(fl.FIXTURE_CSV)
    assert len(binary) >= 8
    assert len(cont) >= 4
    assert all(isinstance(p, float) and isinstance(y, int) for p, y in binary)


def test_load_all_pairs_combined_convenience(tmp_path):
    csv = _write_csv(tmp_path / "d.csv", [
        "risk_binary,p_success,0.6,,1,,observed",
        "benefit_continuous,measured_benefit,,0.3,,0.25,observed",
    ])
    binary, cont = fl.load_all_pairs(csv)
    assert binary == [(0.6, 1)]
    assert cont == [(0.3, 0.25)]
