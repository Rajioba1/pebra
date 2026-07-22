from __future__ import annotations

from e2e.experiments.agent_ab.runners import token_accounting
from e2e.experiments.agent_ab.runners.model_client import ModelTurn


def test_totals_preserve_missing_required_usage_as_unavailable() -> None:
    turns = [
        ModelTurn(input_tokens=10, output_tokens=2),
        ModelTurn(input_tokens=None, output_tokens=3),
    ]

    usage = token_accounting.summarize(turns)

    assert usage["input_tokens"] is None
    assert usage["output_tokens"] == 5
    assert usage["usage_complete"] is False


def test_optional_cache_totals_are_unavailable_not_invented_zero() -> None:
    usage = token_accounting.summarize([
        ModelTurn(input_tokens=10, output_tokens=2),
        ModelTurn(input_tokens=20, output_tokens=3, cache_read_tokens=4),
    ])

    assert usage["input_tokens"] == 30
    assert usage["output_tokens"] == 5
    assert usage["cache_read_tokens"] is None
    assert usage["cache_write_tokens"] is None
    assert usage["usage_complete"] is True


def test_empty_subset_is_labelled_and_unavailable() -> None:
    usage = token_accounting.summarize([], label="understand_turn_usage")

    assert usage == {
        "label": "understand_turn_usage",
        "scope": "whole provider turns requesting or consuming repository_context",
        "turn_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "usage_complete": False,
    }


def test_run_aggregate_preserves_one_missing_arm_as_unavailable() -> None:
    usage = token_accounting.aggregate([
        {
            "turn_count": 2, "input_tokens": 30, "output_tokens": 5,
            "cache_read_tokens": None, "cache_write_tokens": None,
            "usage_complete": True,
        },
        {
            "turn_count": 1, "input_tokens": None, "output_tokens": 2,
            "cache_read_tokens": None, "cache_write_tokens": None,
            "usage_complete": False,
        },
    ])

    assert usage["turn_count"] == 3
    assert usage["input_tokens"] is None
    assert usage["output_tokens"] == 7
    assert usage["usage_complete"] is False


def test_invalid_or_negative_counters_degrade_to_unavailable() -> None:
    per_turn = token_accounting.summarize([
        ModelTurn(input_tokens=True, output_tokens=-1),
    ])
    aggregate = token_accounting.aggregate([{
        "turn_count": 1,
        "input_tokens": True,
        "output_tokens": -1,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "usage_complete": True,
    }])

    assert per_turn["input_tokens"] is None
    assert per_turn["output_tokens"] is None
    assert per_turn["usage_complete"] is False
    assert aggregate["input_tokens"] is None
    assert aggregate["output_tokens"] is None
