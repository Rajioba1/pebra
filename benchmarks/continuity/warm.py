"""Unpaid policy probe for cold, shipped, and repository-local priors.

This module is not a calibration corpus or fit. It exercises the same pure scoring, prior,
learning-snapshot, and decision functions used by production with synthetic evidence. The probe
checks two invariants for the reviewed provisional prior: relevant evidence may improve a safe action's
risk-adjusted utility, while it must not erase an independent harmful event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pebra.core import assessment_builder, decision_engine, prediction_capture
from pebra.core.apply_snapshot import SnapshotBundle, SnapshotFact, apply_snapshot
from pebra.core.calibrated_priors import CALIBRATED_PRIORS
from pebra.core.constants import (
    COLD_START_VARIANCES,
    LEARNED_VARIANCE_FLOOR_RATIO,
    Decision,
)
from pebra.core.language_capability import LanguageCapability
from pebra.core.models import (
    AssessmentInput,
    AssessmentRequest,
    CandidateAction,
    CandidateGraphRiskEvidence,
    ScopedGraphRiskFact,
    SymbolDiffEvidence,
)
from pebra.core.prediction_capture import COST_CONTINUOUS, RISK_BINARY
from pebra.core.warm_prior import apply_warm_prior


@dataclass(frozen=True)
class ProbeRow:
    case_id: str
    prior_source: str
    decision: Decision
    expected_loss: float
    benefit: float
    expected_utility: float
    utility_sd: float
    rau: float
    effective_threshold: float
    review_cost: float
    p_success_variance: float
    p_success_variance_floor: float
    p_success_variance_cap: float
    review_cost_variance: float
    review_cost_variance_floor: float
    review_cost_variance_cap: float
    consequence_risk_floor_applied: bool


def _input(*, harmful: bool) -> AssessmentInput:
    patch = (
        "diff --git a/src/compat.ts b/src/compat.ts\n"
        "--- a/src/compat.ts\n+++ b/src/compat.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )
    request = AssessmentRequest(
        task="Apply a bounded compatibility improvement",
        candidate_actions=[CandidateAction(
            id="a1",
            action_type="edit",
            label="Update the compatibility path",
            expected_files=["src/compat.ts"],
            proposed_patch=patch,
        )],
    )
    event = (
        {"event": "public_api_break", "p_event": 0.50, "elicited_disutility": 0.80}
        if harmful
        else {"event": "test_regression", "p_event": 0.20, "elicited_disutility": 0.40}
    )
    return AssessmentInput(
        request=request,
        action=request.candidate_actions[0],
        events=[event],
        p_success=0.50,
        immediate_benefit=0.50,
        review_cost=0.20,
        criticality_stage="C2",
        criticality_value=0.50,
        edit_confidence_factors={
            "p_success": 0.90,
            "evidence_quality": 0.90,
            "testability": 0.90,
            "reversibility": 0.90,
            "source_reliability": 0.90,
            "scope_control": 0.90,
        },
        thresholds={"max_expected_loss_without_human": 0.20},
        symbol_diff_evidence=SymbolDiffEvidence(
            parsed_patch_available=True,
            changed_symbols=["src/compat.ts::compat"],
            max_change_kind="CONTRACT" if harmful else "BEHAVIORAL",
            visibility="public_api" if harmful else "internal",
            consequential_symbol_changed=harmful,
            structure_tier="codegraph_structural" if harmful else "python_ast",
        ),
        repo_id="synthetic_probe",
        repo_root="/synthetic/probe",
        language_capability=LanguageCapability(
            language="typescript",
            probe_status="measured",
            node_count=10,
            signature_coverage_ratio=1.0,
            visibility_coverage_ratio=1.0,
        ),
        candidate_graph_risk_evidence=CandidateGraphRiskEvidence(
            status="available",
            verified_patch_hash=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            provider="materialized_codegraph",
            facts=(ScopedGraphRiskFact(
                fact_kind="exported_binding_continuity",
                event="public_api_break",
                risk_source="graph_modify_risk",
                owner_node_ids=("owner",),
                confidence=1.0,
            ),),
        ),
    )


def _shipped(inp: AssessmentInput) -> AssessmentInput:
    return apply_warm_prior(inp, CALIBRATED_PRIORS)


def _local(inp: AssessmentInput) -> AssessmentInput:
    def fact(
        fact_id: str,
        target_type: str,
        target_name: str,
        value: float,
        variance: float,
        aleatoric_variance: float,
    ) -> SnapshotFact:
        return SnapshotFact(
            fact_id=fact_id,
            target_type=target_type,
            target_name=target_name,
            scope_kind="action_type",
            scope_value="edit",
            specificity_rank=1,
            value=value,
            sample_size=120,
            calibration_method="synthetic_probe",
            variance=variance,
            aleatoric_variance=aleatoric_variance,
        )

    return apply_snapshot(
        inp,
        SnapshotBundle(
            snapshot_id="synthetic_local_probe",
            facts=(
                fact("p_success", RISK_BINARY, "p_success", 0.88, 0.001, 0.003),
                fact("review_cost", COST_CONTINUOUS, "review_cost", 0.04, 0.001, 0.001),
                fact(
                    "public_api_break",
                    RISK_BINARY,
                    "p_event.public_api_break",
                    0.05,
                    0.0002,
                    0.0008,
                ),
            ),
        ),
    )


def _prior_summary(inp: AssessmentInput, benefit: float) -> dict[str, object]:
    manifest = prediction_capture.build_prediction_manifest(
        p_success=inp.p_success,
        events=inp.events,
        immediate_benefit=inp.immediate_benefit,
        projected_deltas=inp.benefit_delta_evidence.deltas,
        projected_benefit=benefit,
        review_cost=inp.review_cost,
        action_id=inp.action.id,
        applied_snapshot_provenance=inp.applied_snapshot_provenance,
        warm_prior_provenance=inp.warm_prior_provenance,
    )
    return prediction_capture.summarize_prior_provenance(manifest)


def _row(case_id: str, inp: AssessmentInput) -> ProbeRow:
    result = decision_engine.decide(assessment_builder.build_assessment(inp))
    scores = result.scores
    summary = _prior_summary(inp, float(scores["benefit"]))
    p_target = summary["targets"]["p_success"]  # type: ignore[index]
    review_target = summary["targets"]["review_cost"]  # type: ignore[index]
    p_cap = COLD_START_VARIANCES["p_success"]
    review_cap = COLD_START_VARIANCES["review_cost"]
    applied_facts = (inp.applied_snapshot_provenance or {}).get("applied_facts", [])
    return ProbeRow(
        case_id=case_id,
        prior_source=str(summary["source"]),
        decision=result.recommended_decision,
        expected_loss=float(scores["expected_loss"]),
        benefit=float(scores["benefit"]),
        expected_utility=float(scores["expected_utility"]),
        utility_sd=float(scores["utility_sd"]),
        rau=float(scores["rau"]),
        effective_threshold=float(scores["effective_threshold"]),
        review_cost=float(inp.review_cost),
        p_success_variance=float(inp.p_success_variance or p_cap),
        p_success_variance_floor=float(p_target.get("variance_floor", p_cap * LEARNED_VARIANCE_FLOOR_RATIO)),
        p_success_variance_cap=float(p_target.get("variance_cap", p_cap)),
        review_cost_variance=float(inp.review_cost_variance or review_cap),
        review_cost_variance_floor=float(
            review_target.get("variance_floor", review_cap * LEARNED_VARIANCE_FLOOR_RATIO)
        ),
        review_cost_variance_cap=float(review_target.get("variance_cap", review_cap)),
        consequence_risk_floor_applied=any(
            fact.get("safety_constraint") == "consequence_event_non_decreasing"
            for fact in applied_facts
            if isinstance(fact, dict)
        ),
    )


def run_probe() -> dict[str, ProbeRow]:
    rows: dict[str, ProbeRow] = {}
    for label, transform in (
        ("cold", lambda value: value),
        ("shipped", _shipped),
        ("local", lambda value: _local(_shipped(value))),
    ):
        for posture, harmful in (("safe", False), ("harmful", True)):
            case_id = f"{label}_{posture}"
            rows[case_id] = _row(case_id, transform(_input(harmful=harmful)))
    return rows


def to_payload(rows: dict[str, ProbeRow]) -> dict[str, object]:
    return {
        "schema_version": "continuity-warm-probe-v1",
        "evidence_class": "synthetic_policy_probe",
        "calibration_eligible": False,
        "rows": [
            {**asdict(rows[key]), "decision": rows[key].decision.value}
            for key in sorted(rows)
        ],
    }


def to_json(rows: dict[str, ProbeRow]) -> str:
    return json.dumps(to_payload(rows), sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    rows = run_probe()
    payload = to_payload(rows)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
