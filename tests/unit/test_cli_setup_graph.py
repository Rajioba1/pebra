"""M5c.5 — `pebra setup-graph` / `pebra doctor` graph-engine maintenance, over mocked subprocess.

These never touch a real binary: shutil.which + subprocess.run are patched. They assert the command
ORCHESTRATION (install/init/sync/status order, worktree-mismatch repair, read-only doctor) and exit codes.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from pebra.cli import setup_graph as sg

_FRESH = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
          "index": {"reindexRecommended": False}}
_MISMATCH = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
             "index": {"reindexRecommended": False},
             "worktreeMismatch": {"worktreeRoot": "/wt", "indexRoot": "/main"}}
_STALE = {"initialized": True, "pendingChanges": {"added": 0, "modified": 1, "removed": 0},
          "index": {"reindexRecommended": False}}
_REINDEX = {"initialized": True, "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "index": {"reindexRecommended": True}}


class _Engine:
    """Fake subprocess.run for the codegraph CLI: serves a queue of status payloads, records argv."""

    def __init__(self, status_payloads):
        self._status = list(status_payloads)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["codegraph", "status"]:
            payload = self._status.pop(0) if self._status else None
            if payload is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")  # init / sync / npm

    def cmds(self, name):
        return [c for c in self.calls if len(c) > 1 and c[1] == name]


def _patch(monkeypatch, engine, *, codegraph=True, npm=False):
    def which(name):
        if name == "codegraph":
            return "/usr/bin/codegraph" if codegraph else None
        if name == "npm":
            return "/usr/bin/npm" if npm else None
        return None
    monkeypatch.setattr(sg.shutil, "which", which)
    monkeypatch.setattr(sg.subprocess, "run", engine)


def _args(**kw):
    base = {"repo_root": "/repo", "as_json": False, "fix": False, "fix_graph": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_setup_graph_inits_then_syncs_then_verifies(monkeypatch, capsys) -> None:
    eng = _Engine([_FRESH])  # status after init+sync = fresh
    _patch(monkeypatch, eng)
    rc = sg.run_setup_graph(_args())
    assert rc == 0
    assert eng.cmds("init") and eng.cmds("sync") and eng.cmds("status")


def test_setup_graph_advises_install_when_binary_and_npm_absent(monkeypatch, capsys) -> None:
    eng = _Engine([])
    _patch(monkeypatch, eng, codegraph=False, npm=False)
    rc = sg.run_setup_graph(_args())
    assert rc == 1
    assert not eng.cmds("init")  # never tries to init without an engine
    assert "npm install -g @colbymchenry/codegraph" in capsys.readouterr().out


def test_setup_graph_installs_when_npm_present(monkeypatch) -> None:
    # codegraph missing initially; after the (mocked) npm install, which() reports it present
    state = {"installed": False}

    def which(name):
        if name == "codegraph":
            return "/usr/bin/codegraph" if state["installed"] else None
        if name == "npm":
            return "/usr/bin/npm"
        return None

    eng = _Engine([_FRESH])
    monkeypatch.setattr(sg.shutil, "which", which)

    def run(argv, **kwargs):
        if argv[:3] == ["npm", "install", "-g"]:
            state["installed"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return eng(argv, **kwargs)

    monkeypatch.setattr(sg.subprocess, "run", run)
    assert sg.run_setup_graph(_args()) == 0


def test_setup_graph_fix_returns_nonzero_if_mismatch_persists(monkeypatch) -> None:
    eng = _Engine([_MISMATCH])  # even after init+sync, status still shows a mismatch
    _patch(monkeypatch, eng)
    assert sg.run_setup_graph(_args(fix=True)) == 1


def test_doctor_readonly_reports_mismatch_without_repairing(monkeypatch) -> None:
    eng = _Engine([_MISMATCH])
    _patch(monkeypatch, eng)
    rc = sg.run_doctor(_args())
    assert rc == 1
    assert eng.cmds("status") and not eng.cmds("init") and not eng.cmds("sync")  # read-only


@pytest.mark.parametrize("payload", [_STALE, _REINDEX])
def test_doctor_readonly_reports_stale_or_reindex_as_unhealthy(monkeypatch, payload) -> None:
    eng = _Engine([payload])
    _patch(monkeypatch, eng)
    rc = sg.run_doctor(_args())
    assert rc == 1
    assert eng.cmds("status") and not eng.cmds("init") and not eng.cmds("sync")


@pytest.mark.parametrize("payload", [_STALE, _REINDEX])
def test_doctor_fix_graph_repairs_stale_or_reindex(monkeypatch, payload) -> None:
    eng = _Engine([payload, _FRESH])
    _patch(monkeypatch, eng)
    rc = sg.run_doctor(_args(fix_graph=True))
    assert rc == 0
    assert eng.cmds("init") and eng.cmds("sync")


def test_doctor_fix_graph_repairs_worktree_mismatch(monkeypatch) -> None:
    eng = _Engine([_MISMATCH, _FRESH])  # diagnose=mismatch -> repair -> re-status fresh
    _patch(monkeypatch, eng)
    rc = sg.run_doctor(_args(fix_graph=True))
    assert rc == 0
    assert eng.cmds("init") and eng.cmds("sync")


def test_doctor_advises_when_engine_absent(monkeypatch, capsys) -> None:
    eng = _Engine([])
    _patch(monkeypatch, eng, codegraph=False)
    assert sg.run_doctor(_args()) == 1
    assert "not found" in capsys.readouterr().out


def test_setup_graph_json_output(monkeypatch, capsys) -> None:
    eng = _Engine([_FRESH])
    _patch(monkeypatch, eng)
    sg.run_setup_graph(_args(as_json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["command"] == "setup-graph"
