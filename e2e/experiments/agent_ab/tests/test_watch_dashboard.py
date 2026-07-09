"""The watch_dashboard entry point: --once dumps JSON (no server); bad run-ids fail closed."""

from __future__ import annotations

import dataclasses
import json

from e2e.experiments.agent_ab import models
from e2e.experiments.agent_ab.runners import watch_dashboard


def _write_run(ab_out, run_id, outcomes):
    run_dir = ab_out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "outcomes": [dataclasses.asdict(o) for o in outcomes]}
    (run_dir / "outcomes.json").write_text(json.dumps(payload), encoding="utf-8")


def _oc(arm):
    return models.RunOutcome(
        task_id="T1", arm=arm, seed=0, harm_label="risky", harm_materialized=False,
        task_completed=True, over_cautious=False, quality_failure=False, scope_drift=False,
        build_failed=False, test_failed=False, edit_cycle_count=1, advisory_called=False,
        advisory_decision=None, heeded_guidance=None, adherence_state=models.ADH_DID_NOT_CALL,
        blinding_leak=False, blinding_terms=(), timed_out=False,
    )


def test_once_dumps_run_view(tmp_path, monkeypatch, capsys):
    _write_run(tmp_path, "r1", [_oc(models.ARM_CONTROL), _oc(models.ARM_TREATMENT)])
    monkeypatch.setattr(watch_dashboard, "_AB_OUT", tmp_path)
    assert watch_dashboard.main(["--once", "--run-id", "r1"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "r1"


def test_once_dumps_index(tmp_path, monkeypatch, capsys):
    _write_run(tmp_path, "r1", [_oc(models.ARM_CONTROL)])
    monkeypatch.setattr(watch_dashboard, "_AB_OUT", tmp_path)
    assert watch_dashboard.main(["--once"]) == 0
    assert any(r["run_id"] == "r1" for r in json.loads(capsys.readouterr().out)["runs"])


def test_path_like_run_id_fails_closed(capsys):
    assert watch_dashboard.main(["--once", "--run-id", "../escape"]) == 1


def test_once_unknown_run_returns_1(tmp_path, monkeypatch):
    monkeypatch.setattr(watch_dashboard, "_AB_OUT", tmp_path)
    assert watch_dashboard.main(["--once", "--run-id", "ghost"]) == 1


def test_live_open_hash_preserves_mode(tmp_path, monkeypatch):
    seen = {}

    def _serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(watch_dashboard, "_AB_OUT", tmp_path)
    monkeypatch.setattr(watch_dashboard.server, "serve", _serve)

    assert watch_dashboard.main(["--run-id", "r1", "--mode", "assay_js"]) == 0

    assert seen["open_hash"] == "#/run/r1?mode=assay_js"
