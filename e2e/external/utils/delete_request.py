"""Build a DELETE assess request for the external lane — with NO inline fan-in evidence, so CodeGraph
is the only possible source of the file fan-in rollup (the graph-vs-no-graph delta proof).

A DELETE is detected purely from the patch header (``deleted file mode``), so we synthesize a full-file
deletion diff from the file's current content. Pure stdlib; no pebra import (boundary rule).
"""

from __future__ import annotations

import json
from pathlib import Path

GRIDSEARCH_REL = "src/TemplateBlueprint.Controls/Extensions/GridSearchAdapter.cs"

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45, "c3_max_expected_loss_without_human": 0.20,
    "max_p_negative_utility": 0.10, "max_utility_sd_without_human": 0.20,
    "decision_instability_threshold": 0.10, "high_edit_confidence": 0.75, "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}
C3_BUDGET = _THRESHOLDS["c3_max_expected_loss_without_human"]


def build_delete_patch(copy_path: Path | str, rel_path: str = GRIDSEARCH_REL) -> str:
    lines = (Path(copy_path) / rel_path).read_text(encoding="utf-8").splitlines()
    body = "".join(f"-{ln}\n" for ln in lines)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\ndeleted file mode 100644\nindex 1111111..0000000\n"
        f"--- a/{rel_path}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n{body}"
    )


def build_delete_request(copy_path: Path | str, rel_path: str = GRIDSEARCH_REL) -> dict:
    """A C3 delete request. ``symbol_diff``/``blast``/inline fan-in are deliberately ABSENT so the only
    source of the file rollup is CodeGraph (proves the graph drove any escalation)."""
    return {
        "schema_version": "0.1", "task": f"Delete {Path(rel_path).name}", "repo_id": "tpl_e2e",
        "candidate_actions": [{
            "id": "del1", "label": f"Delete {Path(rel_path).name}", "action_type": "edit",
            "affected_symbols": [], "expected_files": [rel_path],
            "proposed_patch": build_delete_patch(copy_path, rel_path),
        }],
        "evidence": {
            "events": [], "p_success": 0.80, "immediate_benefit": 0.30, "review_cost": 0.10,
            "criticality_stage": "C3", "criticality_value": 0.80,
            "edit_confidence_factors": {"p_success": 0.80, "evidence_quality": 0.7, "testability": 0.7,
                                        "reversibility": 0.5, "source_reliability": 0.7,
                                        "scope_control": 0.7},
            "benefit_delta_evidence": {"source_type": "projected", "future_change_exposure": 0.0,
                                       "deltas": {}},
        },
        "thresholds": _THRESHOLDS,
    }


def write_request(request: dict, dest: Path | str) -> Path:
    dest = Path(dest)
    dest.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return dest


def destructive_risk(payload: dict) -> tuple[str, float] | None:
    """Return the dominant structural DELETE event selected by the production risk model."""
    for component in payload["scores"].get("loss_components", []):
        if component["event"] in {"api_contract_break", "public_api_break", "dependency_break"}:
            return component["event"], component["p_event"]
    return None
