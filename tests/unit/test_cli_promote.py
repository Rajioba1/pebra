"""Phase 5 closure — `pebra promote` CLI wiring. Makes the learning loop executable (run_promotion
was fully tested but reachable only from Python). Wiring tested with fakes; no real repo/DB needed."""

from __future__ import annotations

import json

import pytest

from pebra.app.promotion_controller import PromotionResult
from pebra.cli import main
from pebra.cli import promote as promote_cmd


class _Store:
    def close(self):
        pass


class _Repo:
    repo_id = "r"


class _Ctx:
    def __init__(self):
        self.store = _Store()
        self.repo = _Repo()


def _no_rows():
    return PromotionResult(repo_id="r", promoted=False, snapshot_id=None, fact_ids=[],
                           facts_considered=0, facts_promoted=0, facts_vetoed=0,
                           veto_reasons=["NO_CALIBRATION_ROWS"])


def _patch(monkeypatch, *, risk=None, benefit=None, review_cost=None):
    monkeypatch.setattr(promote_cmd.composition, "resolve_repo_and_db", lambda rr, db: _Ctx())
    monkeypatch.setattr(promote_cmd.learning_composition, "build_learning_port", lambda ctx: object())
    monkeypatch.setattr(promote_cmd.promotion_controller, "run_promotion",
                        risk if callable(risk) else lambda repo_id, **kw: risk or _no_rows())
    monkeypatch.setattr(promote_cmd.promotion_controller, "run_benefit_promotion",
                        benefit if callable(benefit) else lambda repo_id, **kw: benefit or _no_rows())
    monkeypatch.setattr(promote_cmd.promotion_controller, "run_review_cost_promotion",
                        review_cost if callable(review_cost)
                        else lambda repo_id, **kw: review_cost or _no_rows())


def test_promote_subcommand_registered():
    args = main.build_parser().parse_args(["promote", "--repo-root", ".", "--db", "x.db"])
    assert args.func is promote_cmd.run


def test_promote_reports_risk_and_benefit(monkeypatch, capsys):
    _patch(monkeypatch, risk=PromotionResult(
        repo_id="r", promoted=True, snapshot_id="rs_1", fact_ids=["lrf_1"],
        facts_considered=3, facts_promoted=1, facts_vetoed=2,
        drift_score=0.12, frozen_due_to_drift=True,
    ))
    rc = promote_cmd.run(main.build_parser().parse_args(["promote", "--json"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["risk"]["snapshot_id"] == "rs_1"
    assert out["risk"]["drift_score"] == pytest.approx(0.12)
    assert out["risk"]["frozen_due_to_drift"] is True
    assert out["review_cost"]["promoted"] is False


def test_promote_no_rows_exits_zero(monkeypatch, capsys):
    # "nothing to promote" is a normal outcome, not an error -> exit 0.
    _patch(monkeypatch)  # both default to no-rows
    rc = promote_cmd.run(main.build_parser().parse_args(["promote"]))
    assert rc == 0


def test_promote_threads_drift_freeze_threshold(monkeypatch):
    seen = {}

    def risk(repo_id, **kw):
        seen["threshold"] = kw["config"].drift_freeze_threshold
        return _no_rows()

    _patch(monkeypatch, risk=risk)
    rc = promote_cmd.run(main.build_parser().parse_args([
        "promote", "--drift-freeze-threshold", "0.25",
    ]))
    assert rc == 0
    assert seen["threshold"] == pytest.approx(0.25)
