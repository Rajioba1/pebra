"""`pebra agent-init` — write the pebra-safe-edit skill / AGENTS.md rules, and optionally a host hook
config. Default mode is instruction-only; ``--with-hook`` adds the pre-edit gate config. Tests use temp
repos — the real repo's .claude/.codex folders are gitignored, so never rely on them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pebra.cli import agent_init
from pebra.cli.main import build_parser

_SKILL_REL = Path(".claude") / "skills" / "pebra-safe-edit" / "SKILL.md"
_TOKENS = ("pebra assess", "pebra verify", "record-outcome", "pre-edit")
_REVISE_TOKENS = ("revise_safer", "proposed_patch", "expected_loss", "resubmit")


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


def test_skill_protocol_requires_reassessing_revise_safer(tmp_path):
    _run("claude", tmp_path)
    body = (tmp_path / _SKILL_REL).read_text(encoding="utf-8")
    for token in _REVISE_TOKENS:
        assert token in body, f"missing {token!r}"
    lowered = body.lower()
    assert "do not apply the original patch" in lowered
    assert "lower risk" in lowered
    assert "permits" in lowered


def test_skill_protocol_omits_dead_candidate_verification_self_report(tmp_path):
    # The trust boundary discards request-supplied evidence.candidate_verification, and safer_route has
    # no candidate_verification field. The shipped protocol must NOT instruct agents to self-report it,
    # or a real agent would cycle revise_safer forever on a genuinely safe, verified edit.
    _run("claude", tmp_path)
    body = (tmp_path / _SKILL_REL).read_text(encoding="utf-8")
    assert "evidence.candidate_verification" not in body
    assert "safer_route.candidate_verification" not in body


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
    # Default (no --with-hook) installs NO hook, so an existing settings.json must be left untouched.
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"custom": true}\n', encoding="utf-8")
    _run("claude", tmp_path)
    assert settings.read_text(encoding="utf-8") == '{"custom": true}\n'


def test_claude_default_creates_no_settings(tmp_path):
    _run("claude", tmp_path)
    assert not (tmp_path / ".claude" / "settings.json").exists()  # instruction-only by default


def _run_with_hook(target: str, repo_root: Path) -> int:
    args = build_parser().parse_args(
        ["agent-init", "--target", target, "--repo-root", str(repo_root), "--with-hook"]
    )
    return agent_init.run_agent_init(args)


def _pre_tool_use(settings_path: Path) -> list:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    return data.get("hooks", {}).get("PreToolUse", [])


def test_claude_with_hook_installs_pretooluse_gate(tmp_path):
    assert _run_with_hook("claude", tmp_path) == 0
    settings = tmp_path / ".claude" / "settings.json"
    assert settings.is_file()
    entries = _pre_tool_use(settings)
    cmds = [h.get("command") for e in entries for h in e.get("hooks", [])]
    assert any("gate-hook" in (c or "") for c in cmds)
    assert any("Edit" in e.get("matcher", "") for e in entries)


def test_claude_with_hook_preserves_existing_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "custom": True,
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]},
    }), encoding="utf-8")
    _run_with_hook("claude", tmp_path)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["custom"] is True  # unrelated settings preserved
    cmds = [h.get("command") for e in data["hooks"]["PreToolUse"] for h in e.get("hooks", [])]
    assert "echo hi" in cmds and any("gate-hook" in (c or "") for c in cmds)  # existing hook kept + gate added


def _codex_pre_tool_use(root: Path) -> list:
    data = json.loads((root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    return data.get("hooks", {}).get("PreToolUse", [])


def test_codex_default_creates_no_hooks_json(tmp_path):
    _run("codex", tmp_path)
    assert not (tmp_path / ".codex" / "hooks.json").exists()  # instruction-only by default


def test_codex_does_not_touch_existing_hooks_json(tmp_path):
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text('{"existing": true}\n', encoding="utf-8")
    _run("codex", tmp_path)  # no --with-hook
    assert hooks.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_codex_with_hook_installs_apply_patch_gate(tmp_path):
    assert _run_with_hook("codex", tmp_path) == 0
    assert not (tmp_path / ".claude" / "settings.json").exists()  # codex uses .codex, not .claude
    hooks_json = tmp_path / ".codex" / "hooks.json"
    assert hooks_json.is_file()
    entries = _codex_pre_tool_use(tmp_path)
    cmds = [h.get("command") for e in entries for h in e.get("hooks", [])]
    assert any("gate-hook" in (c or "") for c in cmds)
    assert any("apply_patch" in e.get("matcher", "") for e in entries)
    assert (tmp_path / "AGENTS.md").is_file()  # codex scaffolding still proceeds


def test_codex_with_hook_reports_best_effort_surface(tmp_path, capsys):
    _run_with_hook("codex", tmp_path)
    out = capsys.readouterr().out.lower()
    assert "best-effort" in out
    assert "verify your codex host loads" in out


def test_codex_with_hook_preserves_existing_and_is_idempotent(tmp_path):
    hooks_json = tmp_path / ".codex" / "hooks.json"
    hooks_json.parent.mkdir(parents=True, exist_ok=True)
    hooks_json.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "shell", "hooks": [{"type": "command", "command": "echo hi"}]}]},
    }), encoding="utf-8")
    _run_with_hook("codex", tmp_path)
    first = hooks_json.read_text(encoding="utf-8")
    cmds = [h.get("command") for e in _codex_pre_tool_use(tmp_path) for h in e.get("hooks", [])]
    assert "echo hi" in cmds and any("gate-hook" in (c or "") for c in cmds)  # existing kept + gate added
    _run_with_hook("codex", tmp_path)  # idempotent
    assert hooks_json.read_text(encoding="utf-8") == first
    gate = [e for e in _codex_pre_tool_use(tmp_path)
            if any("gate-hook" in (h.get("command") or "") for h in e.get("hooks", []))]
    assert len(gate) == 1


def test_claude_with_hook_is_idempotent(tmp_path):
    _run_with_hook("claude", tmp_path)
    first = (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    _run_with_hook("claude", tmp_path)
    second = (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert first == second  # no duplicate gate-hook entry on re-run
    entries = _pre_tool_use(tmp_path / ".claude" / "settings.json")
    gate_entries = [e for e in entries if any("gate-hook" in (h.get("command") or "")
                                              for h in e.get("hooks", []))]
    assert len(gate_entries) == 1
