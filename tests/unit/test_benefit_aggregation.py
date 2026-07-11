import pytest

from pebra.core.benefit_aggregation import aggregate_file_deltas


def test_aggregate_file_deltas_sums_complexity_and_weights_maintainability() -> None:
    result = aggregate_file_deltas({
        "heavy.py": (-1.0, 10.0, 10.0),
        "light.py": (-1.0, 2.0, 1.0),
    })

    assert result["complexity_delta"] == -2.0
    expected = 10.0 * (1.0 + 10.0 / 20.0) + 2.0 * (1.0 + 1.0 / 11.0)
    assert result["maintainability_index_delta"] == pytest.approx(expected)


def test_aggregate_file_deltas_is_order_invariant_and_clamps_bad_weights() -> None:
    left = aggregate_file_deltas({"b": (2.0, -4.0, 0.0), "a": (-1.0, 2.0, -5.0)})
    right = aggregate_file_deltas({"a": (-1.0, 2.0, -5.0), "b": (2.0, -4.0, 0.0)})

    assert left == right
    assert left["complexity_delta"] == 1.0
    assert left["maintainability_index_delta"] == -1.0


def test_adding_positive_file_improvement_cannot_lower_aggregate_benefit() -> None:
    baseline = aggregate_file_deltas({"a": (-1.0, 10.0, 1.0)})
    extended = aggregate_file_deltas({
        "a": (-1.0, 10.0, 1.0),
        "b": (-1.0, 1.0, 100.0),
    })

    assert extended["maintainability_index_delta"] > baseline["maintainability_index_delta"]


def test_single_file_is_identity_regardless_of_exposure_weight() -> None:
    assert aggregate_file_deltas({"a": (-1.0, 10.0, 100.0)}) == {
        "complexity_delta": -1.0,
        "maintainability_index_delta": 10.0,
    }


def test_legacy_unweighted_multifile_callbacks_preserve_averaged_mi_contract() -> None:
    assert aggregate_file_deltas({"a": (-1.0, 4.0, 0.0), "b": (-1.0, 4.0, 0.0)}) == {
        "complexity_delta": -2.0,
        "maintainability_index_delta": 4.0,
    }
