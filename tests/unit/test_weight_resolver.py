"""Architecture §5 — WeightResolver. Pure: normalizes criterion weights with provenance."""

from __future__ import annotations

import pytest

from pebra.core import weight_resolver as wr


def test_missing_weights_fall_back_to_equal_weight_cold_start() -> None:
    resolved = wr.resolve_weights(None, criteria=["benefit", "risk", "effort"])
    assert resolved.weights == {"benefit": pytest.approx(1 / 3),
                                "risk": pytest.approx(1 / 3),
                                "effort": pytest.approx(1 / 3)}
    assert resolved.source == "cold_start"


def test_weights_are_normalized_to_sum_one() -> None:
    resolved = wr.resolve_weights({"a": 2.0, "b": 2.0}, criteria=["a", "b"])
    assert resolved.weights == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}
    assert sum(resolved.weights.values()) == pytest.approx(1.0)


def test_negative_weight_is_rejected() -> None:
    with pytest.raises(ValueError):
        wr.resolve_weights({"a": -1.0, "b": 2.0}, criteria=["a", "b"])


def test_unnormalized_input_reports_warning_without_mutating_decision() -> None:
    resolved = wr.resolve_weights({"a": 0.2, "b": 0.2}, criteria=["a", "b"])
    assert resolved.warnings  # sum != 1 before normalization → consistency warning
    assert sum(resolved.weights.values()) == pytest.approx(1.0)
