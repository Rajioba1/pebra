"""Unit checks for the scripted-agent workflow ordering. No pebra imports."""

from __future__ import annotations

from pathlib import Path

from e2e.utils import agent_harness as ah


def test_pre_edit_cycle_assesses_before_applying_edit(monkeypatch, tmp_path):
    calls: list[str] = []

    monkeypatch.setattr(
        ah.ch,
        "assess",
        lambda request, *, repo_root, db: calls.append("assess") or {
            "assessment_id": "asm_1",
            "model_guidance_packet": {
                "binding": {
                    "required_checks_before_commit": [],
                    "requires_dry_run": False,
                }
            },
        },
    )
    monkeypatch.setattr(ah, "apply_risky_edit", lambda repo: calls.append("apply"))
    monkeypatch.setattr(
        ah.ch,
        "verify",
        lambda assessment_id, *, repo_root, db, completed_checks, dry_run_preview: (
            calls.append("verify") or True,
            {},
        ),
    )
    monkeypatch.setattr(
        ah.ch, "record_outcome", lambda *args, **kwargs: calls.append("record")
    )
    monkeypatch.setattr(
        ah.ch, "learn", lambda *args, **kwargs: calls.append("learn") or {"observed": 1}
    )

    transcript = ah.run_pre_edit_cycle(
        tmp_path, tmp_path / "pebra.db", Path("request.json"), actual_success=False
    )

    assert transcript.assessment_id == "asm_1"
    assert calls == ["assess", "apply", "verify", "record", "learn"]


def test_seed_failed_history_resets_after_each_completed_cycle(monkeypatch, tmp_path):
    calls: list[str] = []

    monkeypatch.setattr(
        ah,
        "run_pre_edit_cycle",
        lambda *args, **kwargs: calls.append("cycle") or ah.AgentTranscript(
            assessment_id="asm_x", payload={}, verify_passed=True, learn_result={}
        ),
    )
    monkeypatch.setattr(ah, "reset_risky_edit", lambda repo: calls.append("reset"))

    ah.seed_failed_history(tmp_path, tmp_path / "pebra.db", Path("request.json"), n=2)

    assert calls == ["cycle", "reset", "cycle", "reset"]
