"""The CanCloseAsync public-API signature change: a scoped interface edit that breaks the build.

Adding a required ``CancellationToken`` parameter to ``IWorkspace.CanCloseAsync`` touches only the
interface file (so post-edit verify stays in-scope), but breaks every implementer/caller at compile
time (CS0535/CS7036) — a real materialized risk the build surfaces. Pure stdlib; no pebra import.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

IWORKSPACE_REL = "src/TemplateBlueprint.Core/Contracts/IWorkspace.cs"
_ORIG = "    Task<bool> CanCloseAsync();"
_BREAK = "    Task<bool> CanCloseAsync(System.Threading.CancellationToken cancellationToken);"

_THRESHOLDS = {
    "max_expected_loss_without_human": 0.45, "c3_max_expected_loss_without_human": 0.20,
    "max_p_negative_utility": 0.10, "max_utility_sd_without_human": 0.20,
    "decision_instability_threshold": 0.10, "high_edit_confidence": 0.75, "low_edit_confidence": 0.50,
    "rau_bands": {"reject_below": 0.0, "borderline_below": 0.15, "strong_at": 0.40},
}


def _git(copy_path: Path | str, *args: str) -> None:
    subprocess.run(["git", "-C", str(copy_path), *args], check=True, capture_output=True, text=True)


def apply_signature_change(copy_path: Path | str) -> None:
    """Apply the breaking signature change to the interface file and stage it."""
    f = Path(copy_path) / IWORKSPACE_REL
    text = f.read_text(encoding="utf-8")
    if _ORIG not in text:
        raise RuntimeError(f"could not find {_ORIG!r} in {f}")
    f.write_text(text.replace(_ORIG, _BREAK), encoding="utf-8")
    _git(copy_path, "add", IWORKSPACE_REL)


def reset_signature_change(copy_path: Path | str) -> None:
    """Restore the interface file to the committed state (index + worktree)."""
    _git(copy_path, "restore", "--staged", "--worktree", IWORKSPACE_REL)


def _patch(copy_path: Path | str) -> str:
    lines = (Path(copy_path) / IWORKSPACE_REL).read_text(encoding="utf-8").splitlines()
    idx = lines.index(_ORIG) + 1  # 1-based line of the declaration
    return (
        f"diff --git a/{IWORKSPACE_REL} b/{IWORKSPACE_REL}\n--- a/{IWORKSPACE_REL}\n"
        f"+++ b/{IWORKSPACE_REL}\n@@ -{idx},1 +{idx},1 @@\n{_ORIG.replace(_ORIG, '-' + _ORIG)}\n"
        f"+{_BREAK}\n"
    )


def _request(copy_path: Path | str, *, action_id: str, task: str) -> dict:
    return {
        "schema_version": "0.1", "task": task, "repo_id": "tpl_e2e",
        "candidate_actions": [{
            "id": action_id, "label": "Change IWorkspace.CanCloseAsync signature",
            "action_type": "edit", "affected_symbols": [f"{IWORKSPACE_REL}::CanCloseAsync"],
            "expected_files": [IWORKSPACE_REL], "proposed_patch": _patch(copy_path),
        }],
        "evidence": {
            "events": [{"event": "public_api_break", "p_event": 0.10, "elicited_disutility": 0.85}],
            "p_success": 0.72, "immediate_benefit": 0.70, "review_cost": 0.10,
            "criticality_stage": "C3", "criticality_value": 0.80,
            "edit_confidence_factors": {"p_success": 0.72, "evidence_quality": 0.74, "testability": 0.72,
                                        "reversibility": 0.80, "source_reliability": 0.80,
                                        "scope_control": 0.82},
            "variance_breakdown": {"p_success": 0.0016, "benefit": 0.0004, "event_losses": 0.0009,
                                   "review_cost": 0.0004, "scenario_variance": 0.0003},
            "benefit_delta_evidence": {"source_type": "projected", "future_change_exposure": 0.0,
                                       "deltas": {}},
            "symbol_diff": {
                "parsed_patch_available": True,
                "changed_symbols": [f"{IWORKSPACE_REL}::CanCloseAsync"],
                "max_change_kind": "BEHAVIORAL", "visibility": "public",
                "consequential_symbol_changed": True,
            },
        },
        "thresholds": _THRESHOLDS,
    }


def build_signature_request(copy_path: Path | str) -> dict:
    return _request(copy_path, action_id="cca1", task="Add CancellationToken to IWorkspace.CanCloseAsync")


def build_followup_request(copy_path: Path | str) -> dict:
    """A DISTINCT but scoring-equivalent follow-up public-API edit, for the post-learning reassess."""
    return _request(copy_path, action_id="cca2",
                    task="Follow-up: refine IWorkspace.CanCloseAsync signature")
