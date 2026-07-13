"""Trusted terminal-outcome finalization: record, measure, then gated promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pebra.app import learning_controller, promotion_controller, record_outcome_controller
from pebra.core import outcome_labels
from pebra.core import promotion_evaluator as pe
from pebra.ports.learning_port import LearningPort
from pebra.ports.store_port import StorePort


@dataclass(frozen=True)
class FinalizationOutcome:
    assessment_id: str
    outcome_recorded: bool
    measurement_recorded: bool
    observed: int | None
    censored: int | None
    promotions: dict[str, promotion_controller.PromotionResult]


def _trusted_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(detail or {})
    result[outcome_labels.LABEL_SOURCE_KEY] = "host"
    return result


def finalize_outcome(
    assessment_id: str,
    status: str,
    *,
    detail: dict[str, Any] | None,
    store: StorePort,
    learning_port: LearningPort,
    promotion_config: pe.PromotionConfig | None = None,
) -> FinalizationOutcome:
    """Idempotently close one assessment from trusted host evidence."""
    expected_detail = _trusted_detail(detail)
    outcomes = store.load_outcomes(assessment_id)
    outcome_recorded = False
    if outcomes:
        latest = outcomes[-1]
        recorded_detail = dict(latest["detail"] or {})
        recorded_detail.setdefault(outcome_labels.LABEL_SOURCE_KEY, "host")
        if latest["terminal_status"] != status or recorded_detail != expected_detail:
            raise ValueError(f"finalization retry for {assessment_id!r} conflicts with recorded outcome")
    else:
        record_outcome_controller.record_outcome(
            assessment_id, status, outcome_port=store, detail=detail, label_source="host"
        )
        outcome_recorded = True

    measurement_recorded = False
    observed: int | None = None
    censored: int | None = None
    if not store.prediction_errors_exist(assessment_id):
        measurement = learning_controller.measure_learning(
            assessment_id, store=store, learning_port=learning_port
        )
        measurement_recorded = True
        observed, censored = measurement.observed, measurement.censored

    repo_id = store.assessment_detail(assessment_id)["content"].get("repo_id", "")
    config = promotion_config or pe.PromotionConfig()
    promotions = {
        "risk": promotion_controller.run_promotion(
            repo_id, store=store, learning_port=learning_port, config=config,
            trigger_key=f"finalize:{assessment_id}:risk",
        ),
        "benefit": promotion_controller.run_benefit_promotion(
            repo_id, store=store, learning_port=learning_port, config=config,
            trigger_key=f"finalize:{assessment_id}:benefit",
        ),
        "review_cost": promotion_controller.run_review_cost_promotion(
            repo_id, store=store, learning_port=learning_port, config=config,
            trigger_key=f"finalize:{assessment_id}:review_cost",
        ),
    }
    return FinalizationOutcome(
        assessment_id=assessment_id,
        outcome_recorded=outcome_recorded,
        measurement_recorded=measurement_recorded,
        observed=observed,
        censored=censored,
        promotions=promotions,
    )
