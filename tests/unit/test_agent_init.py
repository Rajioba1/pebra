"""`pebra agent-init` — Phase 1 scaffolding ONLY: write the passive pebra-safe-edit skill / AGENTS.md
rules that tell an agent to CONSULT PEBRA before edits. Instruction-only (must-consult wording), installs
NO enforcement hook, touches no core/app/composition. Tests use temp repos — the real repo's .claude is
gitignored, so never rely on it.
"""

from __future__ import annotations

from pathlib import Path

from pebra.cli import agent_init
from pebra.cli.main import build_parser

_SKILL_REL = Path(".claude") / "skills" / "pebra-safe-edit" / "SKILL.md"
_TOKENS = ("pebra assess", "pebra verify", "record-outcome", "pre-edit")


def _run(target: str, repo_root: Path) -> int:
    args = build_parser().parse_args(
        ["agent-init", "--target", target, "--repo-root", str(repo_root)]
    )
    assert args.func is agent_init.run_agent_init
    return agent_init.run_agent_init(args)


def test_agent_init_is_registered():
    args = build_parser().parse_args(["agent-init", "--target", "claude", "--repo-root", "."])
    assert args.func is agent_init.run_agent_init


def test_claude_creates_skill_file(tmp_path):
    assert _run("claude", tmp_path) == 0
    skill = tmp_path / _SKILL_REL
    assert skill.is_file()
    body = skill.read_text(encoding="utf-8")
    for token in _TOKENS:
        assert token in body, f"missing {token!r}"


def test_skill_wording_is_consult_not_block(tmp_path):
    _run("claude", tmp_path)
    body = (tmp_path / _SKILL_REL).read_text(encoding="utf-8").lower()
    assert "consult" in body
    assert "block" not in body  # enforcement wording (blocks edits) is a later slice, not Phase 1


def test_codex_creates_agents_md(tmp_path):
    assert _run("codex", tmp_path) == 0
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()
    body = agents.read_text(encoding="utf-8")
    for token in _TOKENS:
        assert token in body, f"missing {token!r}"


def test_codex_creates_agents_skill_file(tmp_path):
    # codex target also writes the documented repo-local skill (.agents/skills), not only AGENTS.md.
    _run("codex", tmp_path)
    skill = tmp_path / ".agents" / "skills" / "pebra-safe-edit" / "SKILL.md"
    assert skill.is_file()
    body = skill.read_text(encoding="utf-8")
    for token in _TOKENS:
        assert token in body, f"missing {token!r}"


def test_codex_preserves_existing_agents_md(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# My Project\n\nCustom house rules here.\n", encoding="utf-8")
    _run("codex", tmp_path)
    after_first = agents.read_text(encoding="utf-8")
    assert "Custom house rules here." in after_first  # existing content preserved
    assert "pebra assess" in after_first  # section appended
    # idempotency WITH pre-existing user content (the splice path, not the empty-file path)
    _run("codex", tmp_path)
    assert agents.read_text(encoding="utf-8") == after_first  # no drift on re-run
    assert after_first.count("BEGIN pebra-safe-edit") == 1  # single managed block


def test_codex_is_idempotent(tmp_path):
    _run("codex", tmp_path)
    first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    _run("codex", tmp_path)
    second = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert first == second  # no drift on re-run
    assert second.count("BEGIN pebra-safe-edit") == 1  # exactly one managed block


def test_claude_is_idempotent(tmp_path):
    _run("claude", tmp_path)
    first = (tmp_path / _SKILL_REL).read_text(encoding="utf-8")
    _run("claude", tmp_path)
    second = (tmp_path / _SKILL_REL).read_text(encoding="utf-8")
    assert first == second


def test_claude_skill_file_is_fully_managed(tmp_path):
    _run("claude", tmp_path)
    skill = tmp_path / _SKILL_REL
    skill.write_text("local edit\n", encoding="utf-8")
    _run("claude", tmp_path)
    assert "local edit" not in skill.read_text(encoding="utf-8")


def test_claude_does_not_touch_existing_settings(tmp_path):
    # Phase 1 installs NO hook, so an existing .claude/settings.json must be left untouched.
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"custom": true}\n', encoding="utf-8")
    _run("claude", tmp_path)
    assert settings.read_text(encoding="utf-8") == '{"custom": true}\n'
