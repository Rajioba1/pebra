"""setup-engines umbrella command."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from pebra.adapters.rca_adapter import RcaCapability
from pebra.cli import main as cli_main
from pebra.cli import setup_engines as se
from pebra.cli.setup_graph import GraphSetupResult
from pebra.core.rca_engine_paths import RCA_ACCEPTED_VERSION, RCA_INSTALL_COMMAND, RCA_SOURCE_REVISION


def _graph(**kw) -> GraphSetupResult:
    base = dict(
        ok=True,
        action="unchanged",
        diagnosis={"healthy": True, "initialized": True, "worktree_mismatch": False},
        version="1.1.1",
        graph_config={"valid": True, "digest": "x"},
        config_restored=True,
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
