"""Reviewed cold-start priors.

The first cell is deliberately narrow and provisional: it was fitted from three distinct exported
function symbols across two modules in the pinned Zod repository. It applies only to TypeScript after
the production graph provider has proved hash-bound exported-binding continuity at the measured full
tier. Local learned snapshots remain more specific and take precedence when present.
"""

from pebra.core.warm_prior import CalibratedPriorCell

CALIBRATED_PRIORS: tuple[CalibratedPriorCell, ...] = (
    CalibratedPriorCell(
        calibration_tag="zod_single_repo_provisional_v1",
        sample_size=3,
        action_type="edit",
        language="typescript",
        language_tier="full",
        graph_fact_kind="exported_binding_continuity",
        graph_event="public_api_break",
        graph_risk_source="graph_modify_risk",
        graph_provider="materialized_codegraph",
        min_graph_confidence=0.90,
        p_success=0.8,
        p_success_variance=0.02666666666666667,
        # Beta(4,1) posterior expected Bernoulli variance. Combined with the parameter variance this
        # retains the reviewed cold predictive-variance cap; three successes do not imply certainty.
        p_success_aleatoric_variance=0.13333333333333333,
    ),
)
