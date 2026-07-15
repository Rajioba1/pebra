from __future__ import annotations

from types import SimpleNamespace

from pebra.app.candidate_apply_controller import CandidateApplyOutcome
from pebra.cli import apply_candidate


def test_apply_candidate_cli_uses_resolved_assessment_and_shared_ports(monkeypatch, capsys) -> None:
    closed = []
    ctx = SimpleNamespace(
        repo=SimpleNamespace(repo_id="repo-1", repo_root="/repo"),
        db_path="/repo/.pebra/pebra.db",
        store=SimpleNamespace(close=lambda: closed.append(True)),
    )
    sentinel_ports = {"replay_cache": object(), "gate": object(), "applier": object()}
    monkeypatch.setattr(apply_candidate.composition, "resolve_repo_and_db", lambda *_: ctx)
    monkeypatch.setattr(
        apply_candidate.composition, "build_candidate_apply_ports", lambda value: sentinel_ports
    )
    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return CandidateApplyOutcome("asm_7", ("src/a.py", "src/b.py"))

    monkeypatch.setattr(apply_candidate.candidate_apply_controller, "apply_candidate", fake_apply)

    rc = apply_candidate.run(SimpleNamespace(
        assessment_id="asm_7", repo_root="/repo", db=None,
    ))

    assert rc == 0
    assert captured == {
        "assessment_id": "asm_7",
        "repo_id": "repo-1",
        "repo_root": "/repo",
        "db_path": "/repo/.pebra/pebra.db",
        "store": ctx.store,
        **sentinel_ports,
    }
    assert closed == [True]
    assert '"assessment_id": "asm_7"' in capsys.readouterr().out
