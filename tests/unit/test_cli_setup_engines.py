"""setup-engines umbrella command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pebra.adapters.rca_adapter import RcaCapability
from pebra.cli import main as cli_main
from pebra.cli import setup_engines as se
from pebra.cli.setup_graph import GraphSetupResult
from pebra.core.graph_snapshot import GraphSnapshot
from pebra.core.rca_engine_paths import RCA_ACCEPTED_VERSION, RCA_INSTALL_COMMAND, RCA_SOURCE_REVISION


def _graph(**kw) -> GraphSetupResult:
    base = dict(
        ok=True,
        action="unchanged",
        diagnosis={"healthy": True, "initialized": True, "worktree_mismatch": False},
        version="1.1.1",
        graph_config={"valid": True, "digest": "x"},
        config_restored=True,
        snapshot=GraphSnapshot(
            status="available",
            provider="CodeGraph",
            provider_version="1.1.1",
            index_version="24",
            repo_head="abc123",
            config_digest="x",
            graph_scope_digest="scope",
            sync_performed=False,
            fallback_reason=None,
        ),
    )
    base.update(kw)
    return GraphSetupResult(**base)


def _rca(*, accepted: bool = True, status: str = "accepted") -> RcaCapability:
    return RcaCapability(
        status=status,
        accepted=accepted,
        version=RCA_ACCEPTED_VERSION if accepted else None,
        accepted_version=RCA_ACCEPTED_VERSION,
        required_source_revision=RCA_SOURCE_REVISION,
        benefit_mode="measured" if accepted else "projected",
        reason=None if accepted else "binary not found",
        remediation_command=RCA_INSTALL_COMMAND,
    )


def _args(**kw):
    base = {"repo_root": "/repo", "fix": False, "via": "auto", "as_json": True}
    base.update(kw)
    return argparse.Namespace(**base)


def test_setup_engines_registered_in_live_parser() -> None:
    parser = cli_main.build_parser()
    names = set(parser._subparsers._group_actions[0].choices)
    assert "setup-engines" in names


@pytest.mark.parametrize(
    "graph_ok,rca_ok,exit_code,ok_flag",
    [
        (True, True, 0, True),
        (True, False, 1, False),
        (False, True, 1, False),
        (False, False, 1, False),
    ],
)
def test_setup_engines_partial_success_matrix(
    monkeypatch, capsys, graph_ok, rca_ok, exit_code, ok_flag
) -> None:
    monkeypatch.setattr(
        se,
        "ensure_graph_ready",
        lambda *a, **k: _graph(
            ok=graph_ok,
            action="unchanged" if graph_ok else "failed",
            diagnosis={"healthy": graph_ok, "initialized": True, "worktree_mismatch": False},
            error=None if graph_ok else "graph failed",
        ),
    )
    monkeypatch.setattr(
        se,
        "probe_rca",
        lambda: _rca(accepted=rca_ok, status="accepted" if rca_ok else "missing"),
    )
    monkeypatch.setattr(
        se,
        "build_rca_remediation",
        lambda: {
            "cargo_available": False,
            "prerequisite": "Install Rust and Cargo with rustup",
            "command": RCA_INSTALL_COMMAND,
        },
    )

    rc = se.run(_args())
    payload = json.loads(capsys.readouterr().out)
    assert rc == exit_code
    assert payload["ok"] is ok_flag
    assert payload["assess_ready"] is True
    assert payload["graph"]["trusted"] is graph_ok
    assert payload["rca"]["accepted"] is rca_ok
    if not rca_ok:
        assert payload["rca"]["benefit_mode"] == "projected"
        assert "--rev" in payload["rca"]["remediation"]["command"]
        assert RCA_SOURCE_REVISION in payload["rca"]["remediation"]["command"]


def test_setup_engines_passes_fix_and_via(monkeypatch, capsys) -> None:
    seen = {}

    def capture(repo, **kw):
        seen.update(kw)
        seen["repo"] = repo
        return _graph()

    monkeypatch.setattr(se, "ensure_graph_ready", capture)
    monkeypatch.setattr(se, "probe_rca", lambda: _rca())
    monkeypatch.setattr(
        se, "build_rca_remediation", lambda: {"cargo_available": True, "prerequisite": None, "command": RCA_INSTALL_COMMAND}
    )
    assert se.run(_args(fix=True, via="npm")) == 0
    assert seen["fix"] is True
    assert seen["via"] == "npm"


def test_setup_engines_json_is_single_document(monkeypatch, capsys) -> None:
    monkeypatch.setattr(se, "ensure_graph_ready", lambda *a, **k: _graph())
    monkeypatch.setattr(se, "probe_rca", lambda: _rca(accepted=False, status="missing"))
    monkeypatch.setattr(
        se,
        "build_rca_remediation",
        lambda: {
            "cargo_available": True,
            "prerequisite": None,
            "command": RCA_INSTALL_COMMAND,
        },
    )
    se.run(_args(as_json=True))
    out = capsys.readouterr().out
    json.loads(out)  # exactly one JSON value
    assert "Traceback" not in out


def test_setup_engines_human_mentions_pinned_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(se, "ensure_graph_ready", lambda *a, **k: _graph())
    monkeypatch.setattr(se, "probe_rca", lambda: _rca(accepted=False, status="missing"))
    monkeypatch.setattr(
        se,
        "build_rca_remediation",
        lambda: {
            "cargo_available": False,
            "prerequisite": "Install Rust and Cargo with rustup",
            "command": RCA_INSTALL_COMMAND,
        },
    )
    se.run(_args(as_json=False))
    text = capsys.readouterr().out
    assert RCA_SOURCE_REVISION in text
    assert "engines are incomplete" in text


@pytest.mark.parametrize("snapshot_status,repo_head", [("unavailable", "abc123"), ("available", None)])
def test_setup_engines_never_calls_unbound_graph_trusted(
    snapshot_status, repo_head, monkeypatch, capsys
) -> None:
    snapshot = replace(
        _graph().snapshot,
        status=snapshot_status,
        repo_head=repo_head,
        fallback_reason="untrusted" if snapshot_status != "available" else None,
    )
    monkeypatch.setattr(se, "ensure_graph_ready", lambda *a, **k: _graph(snapshot=snapshot))
    monkeypatch.setattr(se, "probe_rca", lambda: _rca())
    monkeypatch.setattr(
        se,
        "build_rca_remediation",
        lambda: {"cargo_available": True, "prerequisite": None, "command": RCA_INSTALL_COMMAND},
    )

    assert se.run(_args()) == 1
    assert json.loads(capsys.readouterr().out)["graph"]["trusted"] is False


def test_setup_engines_import_and_execution_do_not_load_forbidden_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    code = r"""
import sys
from argparse import Namespace
from pebra.adapters.rca_adapter import RcaCapability
from pebra.cli import setup_engines as command
from pebra.cli.setup_graph import GraphSetupResult
from pebra.core.graph_snapshot import GraphSnapshot

snapshot = GraphSnapshot(
    status="available", provider="CodeGraph", provider_version="1.1.1",
    index_version="24", repo_head="abc", config_digest="absent",
    graph_scope_digest="scope", sync_performed=False, fallback_reason=None,
)
command.ensure_graph_ready = lambda *a, **k: GraphSetupResult(
    ok=True, action="unchanged",
    diagnosis={"healthy": True, "initialized": True, "worktree_mismatch": False},
    version="1.1.1", graph_config={"valid": True, "digest": "absent"},
    config_restored=True, snapshot=snapshot,
)
command.probe_rca = lambda: RcaCapability(
    status="accepted", accepted=True, version="0.0.25",
    accepted_version="0.0.25", required_source_revision="a" * 40,
    benefit_mode="measured", reason=None, remediation_command="cargo install rca",
)
command.build_rca_remediation = lambda: {
    "cargo_available": True, "prerequisite": None, "command": "cargo install rca"
}
assert command.run(Namespace(repo_root=".", fix=False, via="auto", as_json=True)) == 0
forbidden = (
    "pebra.app.assess_controller", "pebra.cli.assess", "pebra.cli.gate_check",
    "pebra.cli.dashboard", "pebra.cli.tui", "pebra.mcp_server",
)
loaded = sorted(name for name in sys.modules if name.startswith(forbidden))
raise SystemExit(bool(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout or result.stderr
