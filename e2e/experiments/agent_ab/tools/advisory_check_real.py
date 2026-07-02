"""Treatment-arm backing the ``advisory_check`` tool: PEBRA's REAL pre-edit assessment.

Builds a PEBRA assess request from the subject's tool input, shells ``python -m pebra assess`` through
``e2e/utils/cli_harness`` (no ``import pebra`` — boundary rule), and reshapes the payload into the exact
shared advisory shape (advisory_contract.OUTPUT_KEYS) so it is indistinguishable in STRUCTURE from the
sham. Only the CONTENT (a real graph-backed decision) differs.

NOTE: ``advise`` needs a real repo + the pebra CLI (live runner only). The pure ``_shape_output`` — which
enforces the shape/vocab blinding invariant — IS unit-tested (tests/test_advisory_shape.py).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from e2e.experiments.agent_ab.tools import advisory_contract
from e2e.utils import cli_harness

# criticality-neutral thresholds mirroring the external lane requests.
_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45, "c3_max_expected_loss_without_human": 0.20,
    "max_p_negative_utility": 0.10, "max_utility_sd_without_human": 0.20,
    "decision_instability_threshold": 0.10, "high_edit_confidence": 0.75, "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}


def _build_request(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("target_file", "")
    summary = payload.get("change_summary", "proposed change")
    patch = payload.get("proposed_patch", "")
    if not patch:
        raise ValueError("advisory_check requires proposed_patch so PEBRA can assess the intended edit")
    return {
        "schema_version": "0.1", "task": summary, "repo_id": "ab_experiment",
        "candidate_actions": [{
            "id": "ab1", "label": summary, "action_type": "edit",
            "affected_symbols": [], "expected_files": [target] if target else [],
            "proposed_patch": patch,
        }],
        "evidence": {
            "events": [], "p_success": 0.75, "immediate_benefit": 0.5, "review_cost": 0.1,
            "criticality_stage": "C3", "criticality_value": 0.8,
            "edit_confidence_factors": {"p_success": 0.75, "evidence_quality": 0.7, "testability": 0.7,
                                        "reversibility": 0.7, "source_reliability": 0.7,
                                        "scope_control": 0.7},
            "benefit_delta_evidence": {"source_type": "projected", "future_change_exposure": 0.0,
                                       "deltas": {}},
        },
        "thresholds": _THRESHOLDS,
    }


def _risk_level(result: dict[str, Any]) -> str:
    scores = result.get("scores", {})
    el = scores.get("expected_loss")
    if not isinstance(el, (int, float)):
        return "unknown"
    if el >= 0.45:
        return "high"
    if el >= 0.20:
        return "medium"
    return "low"


# Plain-language advisory keyed on the decision ONLY. Hand-written to carry PEBRA's guidance WITHOUT any
# engine-identifying vocabulary (no graph/fan-in/percentile/pebra/codegraph/blast) — otherwise the tool
# output would unblind the treatment arm. PEBRA's own model_guidance summary is NOT passed through
# because it can name the engine's internals.
_ADVISORY_BY_DECISION = {
    "reject": ("This change looks high-risk: many parts of the codebase may depend on what you are "
               "changing. Reconsider making it as proposed, and check everything that references your "
               "target first."),
    "ask_human": ("This change may have wide impact. Inspect the code that depends on your target and "
                  "run the build and tests before committing; consider getting a second opinion."),
    "inspect_first": ("Before editing, inspect the code that depends on your target so you understand "
                      "the impact, then run the build and tests."),
    "test_first": ("Add or run tests covering your target before making this change, so any breakage "
                   "is caught immediately."),
    "proceed": "No significant concerns were detected for this change.",
}
_ADVISORY_DEFAULT = ("Review the code that depends on your target before committing, and run the build "
                     "and tests.")


def _advisory_text(decision: str | None) -> str:
    return _ADVISORY_BY_DECISION.get(decision, _ADVISORY_DEFAULT)


def _shape_output(result: dict[str, Any]) -> dict[str, Any]:
    """PURE: reshape a PEBRA assess result into the shared advisory shape, IDENTICAL in structure to the
    sham. Agent-facing ``detail`` is EMPTY (raw scores/graph provenance stay internal) so neither the key
    set nor any string can reveal the arm. Only the decision/risk VALUES differ from the sham."""
    decision = result.get("recommended_decision")
    return advisory_contract.normalize_output({
        "recommended_decision": decision,
        "risk_level": _risk_level(result),
        "advisory": _advisory_text(decision),
        "detail": {},
    })


def advise(payload: dict[str, Any], *, repo_root: Path | str, db: Path | str) -> dict[str, Any]:
    """Run PEBRA on the proposed change and return the shared, arm-neutral advisory shape."""
    request = _build_request(payload)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(request, fh)
        req_path = fh.name
    try:
        result = cli_harness.assess(req_path, repo_root=repo_root, db=db)
    finally:
        Path(req_path).unlink(missing_ok=True)
    return _shape_output(result)
