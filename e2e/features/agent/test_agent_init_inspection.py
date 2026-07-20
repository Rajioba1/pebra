"""Black-box agent integration inspection through the real CLI boundary."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _agent_init(root: Path, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", target,
            "--repo-root", str(root), *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_installed_host_check_is_real_cli_non_mutating(tmp_path, target):
    installed = _agent_init(tmp_path, target, "--with-hook")
    assert installed.returncode == 0, installed.stderr
    if target == "claude":
        rule = tmp_path / ".claude/rules/pebra-safe-edit.md"
        body = rule.read_text(encoding="utf-8")
        for obligation in ("assess", "mismatched", "candidate hold", "human sanction", "verify"):
            assert obligation in body.lower()
    graph = tmp_path / ".codegraph"
    graph.mkdir()
    (graph / "codegraph.db").write_bytes(b"initialized graph bytes")
    (graph / "status.json").write_text('{"initialized": true}', encoding="utf-8")

    before = _snapshot(tmp_path)
    checked = _agent_init(tmp_path, target, "--check", "--json")
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["command"] == "agent-init"
    assert payload["target"] == target
    assert payload["protocol_version"] == 2
    assert payload["gate_schema_version"] == 1
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert payload["hook"]["state"] == "exact"
    assert payload["declared_support"] == (
        "configured_enforcing" if target == "claude" else "best_effort"
    )
    assert isinstance(payload["effective_enforcement"], dict)
    assert payload["effective_enforcement"]["candidate_bound"] is False
    assert "graph_unverified_read_only" in payload["effective_enforcement"]["reasons"]
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("target", "hook_rel"),
    (("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")),
)
def test_malformed_hook_check_reports_state_without_repair(tmp_path, target, hook_rel):
    hook = tmp_path / hook_rel
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b"{broken\xff")
    before = _snapshot(tmp_path)

    checked = _agent_init(tmp_path, target, "--with-hook", "--check", "--json")

    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["hook"]["state"] == "malformed"
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_json_without_check_is_rejected_without_creating_files(tmp_path, target):
    result = _agent_init(tmp_path, target, "--json")

    assert result.returncode == 2
    assert "--json requires --check" in result.stderr
    assert _snapshot(tmp_path) == {}
