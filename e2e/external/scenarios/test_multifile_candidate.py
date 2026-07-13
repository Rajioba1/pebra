"""Real-toolchain multi-file candidate proof over the production CLI boundary.

No PEBRA imports: the scenario clones the external specimen, builds a real CodeGraph index, assesses
one atomic two-file patch, exercises the write gate, applies the patch, and verifies the staged tree.
"""

from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

from e2e.external.utils import repo_source as rs
from e2e.utils import cli_harness as ch

_VIEW_MODEL = "src/TemplateBlueprint.AppShell/ViewModels/WorkspaceViewModel.cs"
_MANAGER = "src/TemplateBlueprint.AppShell/ViewModels/WorkspaceManager.cs"

_VM_OLD = "        return Task.FromResult(true);"
_VM_NEW = "        return Task.FromResult(!IsDirty || IsDirty);"
_MANAGER_OLD = "        if (!await workspace.CanCloseAsync())"
_MANAGER_NEW = "        if (!await workspace.CanCloseAsync().ConfigureAwait(true))"

_THRESHOLDS = {
    "max_expected_loss_without_human": 1.0,
    "max_p_negative_utility": 1.0,
    "max_utility_sd_without_human": 1.0,
    "decision_instability_threshold": 1.0,
    "high_edit_confidence": 0.50,
    "low_edit_confidence": 0.10,
    "inspect_on_large_repo_blast": False,
    "rau_bands": {"reject_below": -1.0, "borderline_below": -0.5, "strong_at": 0.0},
}


def _file_patch(repo: Path, rel: str, old: str, new: str) -> str:
    before = (repo / rel).read_text(encoding="utf-8")
    assert before.count(old) == 1, f"fixture drift: expected one {old!r} in {rel}"
    after = before.replace(old, new)
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=2,
        )
    )
    return f"diff --git a/{rel} b/{rel}\n{body}"


def _request(patch: str, files: list[str], *, action_id: str) -> dict:
    return {
        "schema_version": "0.1",
        "task": "Make workspace close checks explicit without changing behavior",
        "repo_id": "multifile_e2e",
        "candidate_actions": [
            {
                "id": action_id,
                "label": "Update connected workspace close paths",
                "action_type": "edit",
                "affected_symbols": [],
                "expected_files": files,
                "proposed_patch": patch,
            }
        ],
        "evidence": {
            "events": [
                {"event": "test_regression", "p_event": 0.01, "elicited_disutility": 0.20}
            ],
            "p_success": 0.99,
            "immediate_benefit": 0.95,
            "review_cost": 0.01,
            "criticality_stage": "C1",
            "criticality_value": 0.20,
            "edit_confidence_factors": {
                "p_success": 0.99,
                "evidence_quality": 0.95,
                "testability": 0.95,
                "reversibility": 0.95,
                "source_reliability": 0.95,
                "scope_control": 0.95,
            },
            "variance_breakdown": {
                "p_success": 0.0001,
                "benefit": 0.0001,
                "event_losses": 0.0001,
                "review_cost": 0.0001,
                "scenario_variance": 0.0001,
            },
            "benefit_delta_evidence": {
                "source_type": "projected",
                "future_change_exposure": 0.0,
                "deltas": {},
            },
        },
        "thresholds": _THRESHOLDS,
    }


def _write_request(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _event(payload: dict, name: str) -> dict | None:
    return next(
        (event for event in payload["scores"]["loss_components"] if event["event"] == name),
        None,
    )


def _gate_event(repo: Path, patch: str) -> dict:
    return {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": str(repo)}


def test_multifile_candidate_runs_graph_assess_gate_and_verify_end_to_end(
    external_repo, tmp_path
):
    repo = rs.clone_at_recorded_head(external_repo, tmp_path / "repo")
    ch.setup_graph(repo_root=repo)

    first = _file_patch(repo, _VIEW_MODEL, _VM_OLD, _VM_NEW)
    second = _file_patch(repo, _MANAGER, _MANAGER_OLD, _MANAGER_NEW)
    combined = first + second
    db = tmp_path / "pebra.db"

    single = ch.assess(
        _write_request(tmp_path / "single.json", _request(first, [_VIEW_MODEL], action_id="one")),
        repo_root=repo,
        db=db,
    )
    multi = ch.assess(
        _write_request(
            tmp_path / "multi.json",
            _request(combined, [_VIEW_MODEL, _MANAGER], action_id="both"),
        ),
        repo_root=repo,
        db=db,
    )

    single_aggregate = single["scores"]["candidate_aggregate"]
    aggregate = multi["scores"]["candidate_aggregate"]
    assert multi["graph_provenance"]["graph_freshness"] == "fresh"
    assert single_aggregate["file_count"] == 1
    assert single_aggregate["breadth_bonus"] == 0.0
    assert aggregate["file_count"] == 2
    assert aggregate["resolved_file_count"] == 2
    assert aggregate["unresolved_file_count"] == 0
    assert aggregate["owner_count"] >= 2
    assert aggregate["impacted_node_count"] > 0
    assert aggregate["resolution_coverage"] == 1.0
    assert 0.0 < aggregate["breadth_bonus"] <= 0.08
    assert multi["scores"]["expected_loss"] > single["scores"]["expected_loss"]
    assert _event(multi, "public_api_break")["p_event"] > _event(single, "public_api_break")[
        "p_event"
    ]

    partial_gate = ch.gate_check(_gate_event(repo, first), db=db)
    assert partial_gate["permission"] == "deny"
    assert partial_gate["tier"] == "candidate_incomplete"

    full_gate = ch.gate_check(_gate_event(repo, combined), db=db)
    assert full_gate["permission"] == "allow"
    assert full_gate["tier"] == "consulted"

    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=repo,
        input=combined,
        text=True,
        capture_output=True,
    )
    assert applied.returncode == 0, applied.stderr
    subprocess.run(
        ["git", "add", "--", _VIEW_MODEL, _MANAGER],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    checks = multi["model_guidance_packet"]["binding"]["required_checks_before_commit"]
    passed, verified = ch.verify(
        multi["assessment_id"],
        repo_root=repo,
        db=db,
        completed_checks={str(check): "passed" for check in checks},
        scope="staged",
    )
    assert passed is True
    assert verified["scope_drift_detected"] is False
    assert verified["unexpected_files"] == []
    assert verified["pre_commit_decision"] == "proceed"
