from __future__ import annotations

from types import SimpleNamespace

import pytest

from pebra.cli import accept_risk


def _args():
    return SimpleNamespace(
        sanction_file=None, apply=True, assessment_id=None, repo_root="/repo", db=None,
    )


def _ctx():
    return SimpleNamespace(
        repo=SimpleNamespace(repo_id="repo-1", repo_root="/repo"),
        db_path="/repo/.pebra/pebra.db",
        store=SimpleNamespace(close=lambda: None),
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


def _wire(monkeypatch):
    ctx = _ctx()
    pending = _pending()
    monkeypatch.setattr(accept_risk.composition, "resolve_repo_and_db", lambda *_: ctx)
    monkeypatch.setattr(accept_risk.git_adapter, "head_commit", lambda *_: "head-1")
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
    return pending


def test_apply_mode_refuses_noninteractive_approval(monkeypatch) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr(accept_risk.sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="interactive terminal"):
        accept_risk.run(_args())


def test_apply_mode_displays_math_and_requires_literal_approval(monkeypatch, capsys) -> None:
    pending = _wire(monkeypatch)
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
