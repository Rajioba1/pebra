"""The shared atomic JSON artifact writer used for run-directory outputs (coverage.json, run_status.json,
outcomes.json). tmp-write-then-replace so a poller never sees a half-written file."""

from __future__ import annotations

import json
from pathlib import Path

from e2e.experiments.agent_ab.runners import run_artifacts


def test_writes_readable_json_and_creates_parents(tmp_path):
    p = tmp_path / "a" / "b" / "x.json"
    run_artifacts.atomic_write_json(p, {"a": 1, "b": [1, 2, 3]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}


def test_leaves_no_tmp_file_behind(tmp_path):
    p = tmp_path / "x.json"
    run_artifacts.atomic_write_json(p, {"k": "v"})
    assert list(tmp_path.glob("*.tmp")) == []


def test_overwrites_atomically(tmp_path):
    p = tmp_path / "x.json"
    run_artifacts.atomic_write_json(p, {"v": 1})
    run_artifacts.atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == 2


def test_retries_transient_permission_error_while_replacing(monkeypatch, tmp_path):
    p = tmp_path / "x.json"
    run_artifacts.atomic_write_json(p, {"v": 1})
    real_replace = Path.replace
    calls = 0

    def _temporarily_locked(self, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("temporarily held by a Windows reader")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _temporarily_locked)

    run_artifacts.atomic_write_json(p, {"v": 2})

    assert calls == 2
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == 2
