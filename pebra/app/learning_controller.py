"""learning_controller (Milestone 4d) — the SOLE shadow-measurement writer.

Triggered on its own (``pebra learn``), never from ``assess_controller`` — enforced by the
``assess-no-learning`` import-linter contract. It reads the captured prediction manifest + the
recorded outcome labels (+ measured benefit from verify's guardrails), computes calibration errors
via the pure ``core/prediction_error`` math, and appends shadow ``prediction_errors`` + a shadow
``risk_snapshot``. It MEASURES; it does not reapply anything to a decision (that is Milestone 5).

Imports core/ + ports/ only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pebra.core import outcome_labels, prediction_error
from pebra.ports.learning_port import LearningPort
from pebra.ports.store_port import StorePort


class OutcomeNotRecordedError(ValueError):
    """Raised when measurement is requested before a terminal outcome was recorded."""


@dataclass
class LearningMeasurementOutcome:
    assessment_id: str
    prediction_error_ids: list[str]
    snapshot_id: str
    observed: int
    censored: int


def _benefit_measurements(guardrails: list[dict[str, Any]]) -> tuple[float | None, dict[str, float]]:
    """Pull the measured benefit + per-metric deltas from the latest verify guardrails row (AD-29).
    Absent (no verify yet) -> (None, {}) so benefit targets are censored, not guessed."""
    if not guardrails:
        return None, {}
    latest = guardrails[-1]
    return latest.get("measured_benefit"), dict(latest.get("measured_benefit_deltas") or {})


def measure_learning(
    assessment_id: str,
    *,
    store: StorePort,
    learning_port: LearningPort,
) -> LearningMeasurementOutcome:
    predictions = store.load_predictions(assessment_id)
    if not predictions:
        raise ValueError(f"no prediction manifest captured for {assessment_id!r}")
    outcomes = store.load_outcomes(assessment_id)
    if not outcomes:
        raise OutcomeNotRecordedError(
            f"no terminal outcome recorded for {assessment_id!r} — record one before measuring"
        )
    # Idempotency (Milestone 4d): measuring twice would append a duplicate error set and double-count
    # the scorecard. Refuse a re-measure; re-measurement after new labels is a Milestone 5 concern.
    if store.prediction_errors_exist(assessment_id):
        raise ValueError(
            f"{assessment_id!r} has already been measured (re-measurement is Milestone 5)"
        )

    labels = outcome_labels.extract_labels(outcomes[-1]["detail"])
    detail = store.assessment_detail(assessment_id)
    measured_benefit, measured_deltas = _benefit_measurements(detail["guardrails"])

    rows = prediction_error.build_error_rows(
        predictions, labels, measured_benefit=measured_benefit, measured_deltas=measured_deltas
    )
    observed = sum(1 for r in rows if r["outcome_label_status"] == "observed")
    censored = len(rows) - observed
    repo_id = detail["content"].get("repo_id", "")
    error_ids, snapshot_id = learning_port.write_measurement(
        assessment_id,
        rows,
        repo_id,
        {
            "assessment_id": assessment_id,
            "targets": len(rows),
            "observed": observed,
            "censored": censored,
        },
        "shadow",
    )
    return LearningMeasurementOutcome(
        assessment_id=assessment_id,
        prediction_error_ids=error_ids,
        snapshot_id=snapshot_id,
        observed=observed,
        censored=censored,
    )
