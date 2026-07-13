"""Milestone 4b — outcome label contract. Pure: validate/extract the explicit learning labels that
ride in the record-outcome detail. 'completed' is a lifecycle status, NOT a learning label — the
labels below are what calibration actually scores against, and missing ones become 'censored'.
"""

from __future__ import annotations

import pytest

from pebra.core import outcome_labels


def test_well_formed_labels_validate() -> None:
    detail = {
        "actual_success": True,
        "event_outcomes": {"test_regression": False, "security_sensitive_change": True},
        "benefit_realized": True,
        "actual_review_cost": 0.2,
        "actual_rework_cost": 0,
        "note": "free-form non-label fields are ignored",
    }
    outcome_labels.validate_labels(detail)  # no raise


def test_empty_detail_is_valid() -> None:
    outcome_labels.validate_labels({})
    outcome_labels.validate_labels(None)


@pytest.mark.parametrize(
    "bad",
    [
        {"actual_success": "yes"},               # not a bool
        {"benefit_realized": 1},                  # not a bool (int is not accepted)
        {"event_outcomes": ["test_regression"]},  # not a dict
        {"event_outcomes": {"e": "true"}},        # values must be bool
        {"actual_review_cost": "cheap"},          # not a number
        {"actual_rework_cost": -1},               # negative cost
    ],
)
def test_malformed_labels_raise_valueerror(bad) -> None:
    with pytest.raises(ValueError):
        outcome_labels.validate_labels(bad)


def test_extract_pulls_only_recognized_label_fields() -> None:
    detail = {
        "actual_success": False,
        "event_outcomes": {"test_regression": True},
        "note": "ignored",
    }
    labels = outcome_labels.extract_labels(detail)
    assert labels["actual_success"] is False
    assert labels["event_outcomes"] == {"test_regression": True}
    assert "note" not in labels
    assert "benefit_realized" not in labels  # absent -> not present (caller treats as censored)


def test_agent_supplied_labels_are_censored() -> None:
    detail = {"actual_success": True, "_pebra_label_source": "agent"}
    assert outcome_labels.extract_labels(detail) == {}
