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
    assert payload["protocol_version"] == 4
    assert payload["gate_schema_version"] == 2
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert payload["hook"]["state"] == "exact"
    assert payload["declared_support"] == (
        "configured_enforcing" if target == "claude" else "best_effort"
    )
    assert isinstance(payload["effective_enforcement"], dict)
    assert payload["effective_enforcement"]["candidate_bound"] is False
    assert "graph_unverified_read_only" in payload["effective_enforcement"]["reasons"]
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_protocol_v3_material_is_reported_stale_then_only_managed_content_is_refreshed(
    tmp_path, target,
):
    installed = _agent_init(tmp_path, target)
    assert installed.returncode == 0, installed.stderr
    unrelated = tmp_path / "KEEP.txt"
    unrelated.write_bytes(b"preserve me\r\n")

    if target == "claude":
        managed = (
            tmp_path / ".claude/rules/pebra-safe-edit.md",
            tmp_path / ".claude/skills/pebra-safe-edit/SKILL.md",
        )
        for path in managed:
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "Interpret → Recall verified lessons", "Interpret → Understand"
                ),
                encoding="utf-8",
                newline="",
            )
    else:
        agents = tmp_path / "AGENTS.md"
        agents.write_text(
            "# User rule\r\n\r\n"
            + agents.read_text(encoding="utf-8").replace(
                "Interpret → Recall verified lessons", "Interpret → Understand"
            ),
            encoding="utf-8",
            newline="",
        )
        skill = tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "Interpret → Recall verified lessons", "Interpret → Understand"
            ),
            encoding="utf-8",
            newline="",
        )

    before_check = _snapshot(tmp_path)
    checked = _agent_init(tmp_path, target, "--check", "--json")
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["protocol_version"] == 4
    expected_states = {"current", "modified"} if target == "claude" else {"modified"}
    assert {item["state"] for item in payload["files"]} == expected_states
    assert _snapshot(tmp_path) == before_check

    refreshed = _agent_init(tmp_path, target)
    assert refreshed.returncode == 0, refreshed.stderr
    assert unrelated.read_bytes() == b"preserve me\r\n"
    if target == "codex":
        assert (tmp_path / "AGENTS.md").read_bytes().startswith(b"# User rule\r\n\r\n")

    current = _agent_init(tmp_path, target, "--check", "--json")
    assert current.returncode == 0, current.stderr
    current_payload = json.loads(current.stdout)
    assert {item["state"] for item in current_payload["files"]} == {"current"}


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
