"""Black-box host materialization proof for the provider-neutral Understand phase."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


_HOST_SKILLS = {
    "claude": Path(".claude/skills/pebra-safe-edit/SKILL.md"),
    "codex": Path(".agents/skills/pebra-safe-edit/SKILL.md"),
}


@pytest.mark.parametrize("target", tuple(_HOST_SKILLS))
def test_installed_host_protocol_teaches_one_advisory_exploration_before_assess(
    tmp_path: Path, target: str
) -> None:
    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pebra",
            "agent-init",
            "--target",
            target,
            "--repo-root",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    skill = (tmp_path / _HOST_SKILLS[target]).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())
    lifecycle = "Interpret → Understand → Design → Assess → PEBRA decides → Apply → Verify"
    assert lifecycle in normalized
    assert normalized.index("2. **Understand.**") < normalized.index("4. **Assess (pre-edit).**")
    assert "Read-only explanation or investigation may stop after Understand" in normalized
    assert "PEBRA does not invent the candidate" in normalized
    assert "PEBRA—not the model—decides" in normalized
    assert "Do not repeat equivalent exploration" in normalized
    assert "does not authorize an edit" in normalized
    assert "not trusted PEBRA scoring evidence" in normalized
    assert "ordinary repository search/read tools" in normalized
    for provider_detail in ("codegraph", "mcp", "prompt hook", "provider selector"):
        assert provider_detail not in normalized.lower()

    inspected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pebra",
            "agent-init",
            "--target",
            target,
            "--repo-root",
            str(tmp_path),
            "--check",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["protocol_version"] == 3


def test_installed_claude_and_codex_skills_are_byte_identical(tmp_path: Path) -> None:
    bodies = []
    for target, skill_path in _HOST_SKILLS.items():
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pebra",
                "agent-init",
                "--target",
                target,
                "--repo-root",
                str(tmp_path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        bodies.append((tmp_path / skill_path).read_bytes())
    assert len(set(bodies)) == 1
