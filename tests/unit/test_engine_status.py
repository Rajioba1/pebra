"""Read-only engine-status aggregation."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from pebra.cli import engine_status


def _rca(*, accepted: bool = True):
    return SimpleNamespace(
        accepted=accepted,
        status="accepted" if accepted else "missing",
        reason=None if accepted else "binary not found",
        version="0.0.25" if accepted else None,
        benefit_mode="measured" if accepted else "projected",
        accepted_version="0.0.25",
        required_source_revision="a" * 40,
        sha256=None,
        source_revision=None,
        validation_mode=None,
    )


def test_collect_engine_status_is_read_only_and_reports_all_rows(monkeypatch) -> None:
    monkeypatch.setattr(engine_status.git_adapter, "head_commit", lambda _root: "abc123")
    monkeypatch.setattr(engine_status.sg, "_installed", lambda: True)
    monkeypatch.setattr(engine_status.sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(
        engine_status.sg,
        "_status",
        lambda _root: {
            "initialized": True,
            "version": "1.1.1",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
            "worktreeMismatch": None,
        },
    )
    monkeypatch.setattr(engine_status, "probe_rca", lambda: _rca())
    monkeypatch.setattr(engine_status, "_probe_bandit", lambda: (True, "1.9.4"))

    payload = engine_status.collect_engine_status("/repo")

    assert set(payload) == {"engines_ok", "git", "codegraph", "rca", "bandit"}
    assert payload["engines_ok"] is True
    assert payload["git"]["head_present"] is True
    assert payload["codegraph"]["mode"] == "available"
    assert payload["rca"]["mode"] == "available"
    assert payload["bandit"]["mode"] == "available"
    assert payload["bandit"]["version"] == "1.9.4"
    assert "/repo" not in repr(payload)


def test_collect_engine_status_degrades_without_git_graph_rca_or_bandit(monkeypatch) -> None:
    monkeypatch.setattr(engine_status.git_adapter, "head_commit", lambda _root: None)
    monkeypatch.setattr(engine_status.sg, "_installed", lambda: False)
    monkeypatch.setattr(engine_status, "probe_rca", lambda: _rca(accepted=False))
    monkeypatch.setattr(engine_status, "_probe_bandit", lambda: (False, None))

    payload = engine_status.collect_engine_status("/repo")

    assert payload["engines_ok"] is False
    for name in ("git", "codegraph", "rca", "bandit"):
        assert payload[name]["mode"] == "degraded"


def test_collect_engine_status_rejects_out_of_range_provider_status(monkeypatch) -> None:
    monkeypatch.setattr(engine_status.git_adapter, "head_commit", lambda _root: "abc123")
    monkeypatch.setattr(engine_status.sg, "_installed", lambda: True)
    monkeypatch.setattr(engine_status.sg, "_installed_version", lambda: "1.1.1")
    monkeypatch.setattr(
        engine_status.sg,
        "_status",
        lambda _root: {
            "initialized": True,
            "version": "9.9.9",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "index": {"reindexRecommended": False, "builtWithExtractionVersion": 24},
            "worktreeMismatch": None,
        },
    )
    monkeypatch.setattr(engine_status, "probe_rca", lambda: _rca())
    monkeypatch.setattr(engine_status, "_probe_bandit", lambda: (True, "1.9.4"))

    payload = engine_status.collect_engine_status("/repo")

    assert payload["codegraph"]["mode"] == "degraded"
    assert payload["codegraph"]["status_version_in_range"] is False
    assert payload["engines_ok"] is False


def test_probe_bandit_degrades_when_module_entry_point_fails(monkeypatch) -> None:
    monkeypatch.setattr(engine_status.importlib.metadata, "version", lambda name: "1.9.4")
    monkeypatch.setattr(
        engine_status.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="broken"),
    )

    assert engine_status._probe_bandit() == (False, "1.9.4")
