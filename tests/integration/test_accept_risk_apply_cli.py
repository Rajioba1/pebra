from __future__ import annotations

from types import SimpleNamespace

import pytest

from pebra.app.candidate_apply_controller import CandidateApplyError
from pebra.app.human_approval_controller import HumanApprovalError
from pebra.cli import accept_risk


def _args():
    return SimpleNamespace(
        sanction_file=None, apply=True, assessment_id=None, repo_root="/repo", db=None,
    )


def _ctx():
    store = SimpleNamespace(close=lambda: None, closed=False)

    def _close():
        store.closed = True

    store.close = _close
    return SimpleNamespace(
        repo=SimpleNamespace(repo_id="repo-1", repo_root="/repo"),
        db_path="/repo/.pebra/pebra.db",
        store=store,
    )


def _pending():
    return SimpleNamespace(
        summary={
            "assessment_id": "asm_7", "task": "preserve API", "files": ["src/a.ts"],
            "risk_benefit": {
                "expected_loss": 0.4, "benefit": 0.3, "expected_utility": -0.1, "rau": -0.2,
            },
            "reason": "shared contract risk", "required_controls": ["compatibility review"],
        },
        replay=SimpleNamespace(request=object()),
    )


def _wire(monkeypatch, *, head="head-1"):
    ctx = _ctx()
    pending = _pending()
    monkeypatch.setattr(accept_risk.composition, "resolve_repo_and_db", lambda *_: ctx)
    monkeypatch.setattr(accept_risk.git_adapter, "head_commit", lambda *_: head)
    monkeypatch.setattr(
        accept_risk.composition, "build_candidate_apply_ports",
        lambda *_: {"replay_cache": object(), "gate": object(), "applier": object()},
    )
    monkeypatch.setattr(
        accept_risk.human_approval_controller, "select_pending_approval", lambda **kw: pending
    )
    monkeypatch.setattr(
        accept_risk.composition, "build_assess_ports", lambda *_: {"sanction_port": object()}
    )
    return ctx, pending


def test_apply_mode_refuses_noninteractive_approval(monkeypatch, capsys) -> None:
    ctx, _ = _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: False)

    assert accept_risk.run(_args()) == 2
    err = capsys.readouterr().err
    assert "accept-risk: human approval requires an interactive terminal" in err
    assert "Traceback" not in err
    assert ctx.store.closed is True


def test_apply_mode_missing_head_returns_controlled_error(monkeypatch, capsys) -> None:
    ctx, _ = _wire(monkeypatch, head=None)

    assert accept_risk.run(_args()) == 2
    err = capsys.readouterr().err
    assert "accept-risk: the current Git HEAD could not be resolved" in err
    assert "Traceback" not in err
    assert "changed during human review" not in err
    assert ctx.store.closed is True


def test_apply_mode_moved_head_returns_distinct_message(monkeypatch, capsys) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "APPROVE")
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda *a, **kw: (_ for _ in ()).throw(
            HumanApprovalError("repository HEAD changed during human review; reassess first")
        ),
    )

    assert accept_risk.run(_args()) == 2
    err = capsys.readouterr().err
    assert "accept-risk: repository HEAD changed during human review; reassess first" in err
    assert "Traceback" not in err


def test_apply_mode_other_human_approval_error_preserves_message(monkeypatch, capsys) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "APPROVE")
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda *a, **kw: (_ for _ in ()).throw(
            HumanApprovalError("no pending assessment requires human approval")
        ),
    )

    assert accept_risk.run(_args()) == 2
    err = capsys.readouterr().err
    assert "accept-risk: no pending assessment requires human approval" in err
    assert "Traceback" not in err


def test_apply_mode_candidate_apply_error_returns_two(monkeypatch, capsys) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "APPROVE")
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda *a, **kw: (_ for _ in ()).throw(CandidateApplyError("candidate apply failed")),
    )

    assert accept_risk.run(_args()) == 2
    err = capsys.readouterr().err
    assert "accept-risk: candidate apply failed" in err
    assert "Traceback" not in err


def test_apply_mode_unexpected_value_error_propagates(monkeypatch) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "APPROVE")
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("programming defect")),
    )

    with pytest.raises(ValueError, match="programming defect"):
        accept_risk.run(_args())


def test_apply_mode_displays_math_and_requires_literal_approval(monkeypatch, capsys) -> None:
    _, pending = _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "APPROVE")
    captured = {}
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda selected, **kw: captured.update(selected=selected, **kw) or SimpleNamespace(
            sanction_id="sx_1", reassessment_id="asm_8", changed_files=("src/a.ts",)
        ),
    )

    assert accept_risk.run(_args()) == 0

    output = capsys.readouterr().out
    assert "Expected loss: 0.4" in output
    assert "Benefit: 0.3" in output
    assert "RAU: -0.2" in output
    assert '"reassessment_id": "asm_8"' in output
    assert captured["selected"] is pending


def test_apply_mode_cancellation_does_not_create_sanction(monkeypatch) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    monkeypatch.setattr(
        accept_risk.human_approval_controller,
        "approve_and_apply",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not approve")),
    )

    assert accept_risk.run(_args()) == 1
