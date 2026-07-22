"""Black-box host materialization proof for the provider-neutral agent lifecycle."""

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
    lifecycle = (
        "Interpret → Recall verified lessons → Retrieve current repository context → Design → "
        "Assess → Calculate → Evaluate gates → Decide → Enforce → Apply → Verify → "
        "Record → Learn/promote"
    )
    assert lifecycle in normalized
    assert normalized.index("2. **Recall verified lessons.**") < normalized.index(
        "5. **Assess (pre-edit).**"
    )
    assert "Read-only explanation or investigation may stop after current-context retrieval" in normalized
    assert "PEBRA does not invent the candidate" in normalized
    assert "PEBRA—not the agent—calculates" in normalized
    assert "Do not repeat equivalent exploration" in normalized
    assert "does not authorize an edit" in normalized
    assert "not trusted PEBRA scoring evidence" in normalized
    assert "ordinary repository search/read tools" in normalized
    for provider_detail in (
        "codegraph", "agentmemory", "mcp", "prompt hook", "provider selector",
        "localhost:", "token savings",
    ):
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
    assert json.loads(inspected.stdout)["protocol_version"] == 4


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


@pytest.mark.parametrize("target", tuple(_HOST_SKILLS))
def test_installed_host_protocol_stages_only_apply_result_before_verify(
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

    normalized = " ".join(
        (tmp_path / _HOST_SKILLS[target]).read_text(encoding="utf-8").split()
    )
    assert "For both apply paths, stage exactly the returned `changed_files`" in normalized
    assert "`git --literal-pathspecs add -- <changed_file>...`" in normalized
    assert "The `--` delimiter alone ends options" in normalized
    assert "does not make wildcard pathspecs literal" in normalized
    assert "never concatenate or evaluate path text as shell code" in normalized
    assert "use no other staging method" in normalized
    assert (
        "Do not run `pebra verify --scope staged` unless the staged path set exactly equals "
        "`changed_files`"
    ) in normalized
