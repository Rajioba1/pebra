"""Registry-wide host integration proof through the real CLI process boundary."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


_SEMANTIC_TOKENS = (
    "pebra assess",
    "revise_safer",
    "trusted human or host",
    "apply-candidate --assessment-id",
    "pebra verify",
    "record-outcome",
)


def _agent_init(root: Path, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pebra",
            "agent-init",
            "--target",
            target,
            "--repo-root",
            str(root),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("target", "skill_path", "instruction_path", "expected_support"),
    (
        (
            "claude",
            ".claude/skills/pebra-safe-edit/SKILL.md",
            ".claude/rules/pebra-safe-edit.md",
            "configured_enforcing",
        ),
        (
            "codex",
            ".agents/skills/pebra-safe-edit/SKILL.md",
            "AGENTS.md",
            "best_effort",
        ),
    ),
)
def test_registry_host_installs_and_inspects_over_process_boundary(
    tmp_path, target, skill_path, instruction_path, expected_support
) -> None:
    sentinel = "# existing host instructions\n"
    if target == "codex":
        (tmp_path / "AGENTS.md").write_text(sentinel, encoding="utf-8")

    installed = _agent_init(tmp_path, target, "--with-hook")
    assert installed.returncode == 0, installed.stderr
    skill = (tmp_path / skill_path).read_text(encoding="utf-8")
    for token in _SEMANTIC_TOKENS:
        assert token in skill
    if target == "claude":
        assert (tmp_path / instruction_path).is_file()
    else:
        agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert agents.startswith(sentinel)
        assert "<!-- BEGIN pebra-safe-edit" in agents

    before = _snapshot(tmp_path)
    checked = _agent_init(tmp_path, target, "--check", "--json")
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["target"] == target
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert payload["hook"]["state"] == "exact"
    assert payload["declared_support"] == expected_support
    assert _snapshot(tmp_path) == before
