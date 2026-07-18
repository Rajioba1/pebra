from __future__ import annotations

import json
import subprocess
import sys

import pytest

_MARK_BEGIN = "<!-- BEGIN pebra-safe-edit (managed by `pebra agent-init`) -->"
_MARK_END = "<!-- END pebra-safe-edit -->"


@pytest.mark.parametrize(
    ("target", "config_rel", "skill_rel"),
    (
        ("claude", ".claude/settings.json", ".claude/skills/pebra-safe-edit/SKILL.md"),
        ("codex", ".codex/hooks.json", ".agents/skills/pebra-safe-edit/SKILL.md"),
    ),
)
@pytest.mark.parametrize(
    "raw",
    (
        "{broken",
        '{"hooks": null}',
        '{"hooks": {"PreToolUse": null}}',
    ),
)
def test_agent_init_malformed_hook_is_failure_atomic(
    tmp_path, target, config_rel, skill_rel, raw,
):
    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    config.write_text(raw, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", target,
            "--repo-root", str(tmp_path), "--with-hook",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 2
    assert config.read_text(encoding="utf-8") == raw
    assert not (tmp_path / skill_rel).exists()
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.parametrize(
    ("target", "config_rel", "skill_rel", "matcher"),
    (
        (
            "claude", ".claude/settings.json", ".claude/skills/pebra-safe-edit/SKILL.md",
            "Edit|Write|MultiEdit",
        ),
        ("codex", ".codex/hooks.json", ".agents/skills/pebra-safe-edit/SKILL.md", "apply_patch"),
    ),
)
def test_agent_init_preserves_lookalike_and_adds_exact_owned_hook(
    tmp_path, target, config_rel, skill_rel, matcher,
):
    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    lookalike = {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
    }
    config.write_text(json.dumps({"hooks": {"PreToolUse": [lookalike]}}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", target,
            "--repo-root", str(tmp_path), "--with-hook",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 0
    entries = json.loads(config.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert lookalike in entries
    exact = {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": "pebra gate-hook"}],
    }
    assert sum(entry == exact for entry in entries) == 1
    assert (tmp_path / skill_rel).is_file()


@pytest.mark.parametrize(
    "raw",
    (
        f"user text\n{_MARK_BEGIN}\nunterminated\n",
        f"{_MARK_END}\nuser text\n{_MARK_BEGIN}\n",
        f"{_MARK_BEGIN}\na\n{_MARK_END}\n{_MARK_BEGIN}\nb\n{_MARK_END}\n",
        f"user text\n{_MARK_END}\n",
        f"{_MARK_BEGIN}\na\n{_MARK_BEGIN}\nb\n{_MARK_END}\n",
    ),
)
def test_codex_corrupt_agents_markers_are_failure_atomic(tmp_path, raw):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(raw, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", "codex",
            "--repo-root", str(tmp_path), "--with-hook",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 2
    assert agents.read_text(encoding="utf-8") == raw
    assert not (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").exists()
    assert not (tmp_path / ".codex/hooks.json").exists()
