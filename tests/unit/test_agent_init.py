"""`pebra agent-init` — write the pebra-safe-edit skill / AGENTS.md rules, and optionally a host hook
config. Default mode is instruction-only; ``--with-hook`` adds the pre-edit gate config. Tests use temp
repos — the real repo's .claude/.codex folders are gitignored, so never rely on them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
from types import SimpleNamespace

import pytest

from pebra.cli import agent_init
from pebra.cli.main import build_parser
from pebra.core import agent_hook_contract
from pebra.core.agent_hosts import AGENT_HOSTS
from pebra.core.constants import Decision

_SKILL_REL = Path(".claude") / "skills" / "pebra-safe-edit" / "SKILL.md"
_CLAUDE_RULE_REL = Path(".claude/rules/pebra-safe-edit.md")
_TOKENS = ("pebra assess", "pebra verify", "record-outcome", "pre-edit")
_REVISE_TOKENS = ("revise_safer", "proposed_patch", "expected_loss", "resubmit")
_OBLIGATIONS = (
    "assess before",
    "mismatched or incomplete candidate",
    "candidate hold or human review",
    "human sanction",
    "verify and record",
)


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


def test_claude_writes_always_loaded_non_negotiables(tmp_path):
    assert _run("claude", tmp_path) == 0
    generated = (tmp_path / _CLAUDE_RULE_REL).read_bytes()
    assert generated == agent_init._CLAUDE_RULE_MD.encode("utf-8")
    body = generated.decode("utf-8").lower()
    for obligation in _OBLIGATIONS:
        assert obligation in body


def test_claude_non_negotiables_match_detailed_protocol_guarantees():
    required_relations = (
        r"\bassess before every significant edit,\s+rename,\s+or delete\b",
        r"\bapply only (?:the )?exact assessed candidate\b",
        (
            r"\bcandidate hold or human review\s+overrides\s+an earlier advisory proceed"
            r"[^.]*\bdoes not cancel\b[^.]*\buser's requested goal\b"
        ),
        (
            r"\b(?:never|do not)\b(?=[^.]*\b(?:create|claim|answer)\b)"
            r"(?=[^.]*\bsanction\b)(?=[^.]*\b(?:yourself|own)\b)[^.]*"
        ),
        r"\bafter application,\s+verify and record (?:the )?outcome\b",
    )

    for body in (agent_init._CLAUDE_RULE_MD.lower(), agent_init._PROTOCOL_BODY.lower()):
        for relation in required_relations:
            assert re.search(relation, body), relation


def test_claude_rule_is_fully_managed_and_idempotent(tmp_path):
    assert _run("claude", tmp_path) == 0
    rule = tmp_path / _CLAUDE_RULE_REL
    original = rule.read_bytes()

    rule.write_text("local edit\n", encoding="utf-8")
    assert _run("claude", tmp_path) == 0
    assert rule.read_bytes() == original

    assert _run("claude", tmp_path) == 0
    assert rule.read_bytes() == original


def test_full_host_skills_are_byte_identical(tmp_path):
    assert _run("claude", tmp_path) == 0
    claude = (tmp_path / _SKILL_REL).read_bytes()
    assert _run("codex", tmp_path) == 0
    codex = (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").read_bytes()
    expected = agent_init._SKILL_MD.encode("utf-8")
    assert claude == expected
    assert codex == expected


def test_detailed_protocol_names_every_live_decision():
    for decision in Decision:
        assert decision.value in agent_init._PROTOCOL_BODY


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
    assert "stable action id" in lowered
    assert "same task text" in lowered
    assert "compatibility-preserving" in lowered
    assert "alias" in lowered
    assert "wrapper" in lowered


def test_skill_protocol_omits_dead_candidate_verification_self_report(tmp_path):
    # The trust boundary discards request-supplied evidence.candidate_verification, and safer_route has
    # no candidate_verification field. The shipped protocol must NOT instruct agents to self-report it,
    # or a real agent would cycle revise_safer forever on a genuinely safe, verified edit.
    _run("claude", tmp_path)
    body = (tmp_path / _SKILL_REL).read_text(encoding="utf-8")
    assert "evidence.candidate_verification" not in body
    assert "safer_route.candidate_verification" not in body


def test_skill_protocol_uses_json_driven_trusted_human_approval_cycle(tmp_path):
    _run("claude", tmp_path)
    body = (tmp_path / _SKILL_REL).read_text(encoding="utf-8").lower()
    assert "next_action" in body
    assert "trusted human or host" in body
    assert "pebra accept-risk" in body
    assert "pebra accept-risk --apply" in body
    assert "pebra apply-candidate --assessment-id" in body
    assert "reassess the exact candidate" in body
    assert "do not create or claim the sanction yourself" in body
    assert "pebra verify" in body


def test_codex_creates_agents_md(tmp_path):
    assert _run("codex", tmp_path) == 0
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()
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


def test_codex_append_preserves_trailing_blank_lines_as_exact_prefix(tmp_path):
    agents = tmp_path / "AGENTS.md"
    original = b"# My Project\n\nCustom house rules here.\n\n\n"
    agents.write_bytes(original)

    assert _run("codex", tmp_path) == 0

    after = agents.read_bytes()
    assert after.startswith(original)
    assert after.count(agent_init._MARK_BEGIN.encode()) == 1


def test_codex_replaces_lf_managed_block_without_changing_user_bytes(tmp_path):
    agents = tmp_path / "AGENTS.md"
    prefix = b"# My Project\n\nUser prefix.\n"
    suffix = b"\nUser suffix.\n\n"
    old_block = (
        agent_init._MARK_BEGIN.encode()
        + b"\nold managed content\n"
        + agent_init._MARK_END.encode()
    )
    agents.write_bytes(prefix + old_block + suffix)

    assert _run("codex", tmp_path) == 0

    after = agents.read_bytes()
    assert after.startswith(prefix)
    assert after.endswith(suffix)
    inserted = after[len(prefix):-len(suffix)]
    assert b"old managed content" not in inserted
    assert inserted.startswith(agent_init._MARK_BEGIN.encode())
    assert inserted.endswith(agent_init._MARK_END.encode())
    assert b"\r\n" not in inserted


def test_codex_replaces_crlf_managed_block_without_changing_user_bytes(tmp_path):
    agents = tmp_path / "AGENTS.md"
    prefix = b"# My Project\r\n\r\nUser prefix.\r\n"
    suffix = b"\r\nUser suffix with a lone LF.\nFinal line.\r\n"
    old_block = (
        agent_init._MARK_BEGIN.encode()
        + b"\r\nold managed content\r\n"
        + agent_init._MARK_END.encode()
    )
    agents.write_bytes(prefix + old_block + suffix)

    assert _run("codex", tmp_path) == 0

    after = agents.read_bytes()
    assert after.startswith(prefix)
    assert after.endswith(suffix)
    inserted = after[len(prefix):-len(suffix)]
    assert b"old managed content" not in inserted
    assert inserted.startswith(agent_init._MARK_BEGIN.encode())
    assert inserted.endswith(agent_init._MARK_END.encode())
    assert b"\r\n" in inserted
    assert b"\n" not in inserted.replace(b"\r\n", b"")


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


def test_claude_with_hook_preserves_lookalike_gate_hook_command(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    lookalike = {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
    }
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [lookalike]}}), encoding="utf-8")

    assert _run_with_hook("claude", tmp_path) == 0

    entries = _pre_tool_use(settings)
    assert lookalike in entries
    assert sum(
        entry == agent_init.managed_hook_entry("Edit|Write|MultiEdit") for entry in entries
    ) == 1


def test_codex_with_hook_preserves_lookalike_gate_hook_command(tmp_path):
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    lookalike = {
        "matcher": "apply_patch",
        "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
    }
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [lookalike]}}), encoding="utf-8")

    assert _run_with_hook("codex", tmp_path) == 0

    entries = _codex_pre_tool_use(tmp_path)
    assert lookalike in entries
    assert sum(entry == agent_init.managed_hook_entry("apply_patch") for entry in entries) == 1


def test_hook_command_is_the_installed_v2_compatibility_contract():
    assert agent_hook_contract.HOOK_COMMAND == "pebra gate-hook"


@pytest.mark.parametrize(
    "raw",
    (
        "{broken",
        "null",
        "[]",
        '{"hooks": []}',
        '{"hooks": null}',
        '{"hooks": {"PreToolUse": {}}}',
        '{"hooks": {"PreToolUse": null}}',
    ),
)
def test_agent_init_with_hook_rejects_invalid_config_without_any_write(
    tmp_path, raw, capsys,
):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(raw, encoding="utf-8")

    assert _run_with_hook("claude", tmp_path) == 2

    assert settings.read_text(encoding="utf-8") == raw
    assert not (tmp_path / _SKILL_REL).exists()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert str(settings) in capsys.readouterr().err


@pytest.mark.parametrize(
    "raw",
    (
        "{broken",
        "null",
        "[]",
        '{"hooks": []}',
        '{"hooks": null}',
        '{"hooks": {"PreToolUse": {}}}',
        '{"hooks": {"PreToolUse": null}}',
    ),
)
def test_codex_agent_init_with_hook_rejects_invalid_config_without_any_write(
    tmp_path, raw, capsys,
):
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(raw, encoding="utf-8")

    assert _run_with_hook("codex", tmp_path) == 2

    assert hooks.read_text(encoding="utf-8") == raw
    assert not (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert str(hooks) in capsys.readouterr().err


@pytest.mark.parametrize(
    ("target", "config_rel"),
    (
        ("claude", ".claude/settings.json"),
        ("codex", ".codex/hooks.json"),
    ),
)
def test_agent_init_with_hook_preserves_valid_settings(tmp_path, target, config_rel):
    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    original = {
        "custom": {"enabled": True},
        "hooks": {"PreToolUse": [{
            "matcher": "Read",
            "hooks": [{"type": "command", "command": "echo ok"}],
        }]},
    }
    config.write_text(json.dumps(original), encoding="utf-8")

    assert _run_with_hook(target, tmp_path) == 0

    after = json.loads(config.read_text(encoding="utf-8"))
    assert after["custom"] == original["custom"]
    assert original["hooks"]["PreToolUse"][0] in after["hooks"]["PreToolUse"]


@pytest.mark.parametrize(
    ("target", "config_rel", "matcher", "skill_rel"),
    (
        (
            "claude",
            ".claude/settings.json",
            "Edit|Write|MultiEdit",
            ".claude/skills/pebra-safe-edit/SKILL.md",
        ),
        ("codex", ".codex/hooks.json", "apply_patch", ".agents/skills/pebra-safe-edit/SKILL.md"),
    ),
)
@pytest.mark.parametrize("state", ("malformed", "conflicting"))
def test_with_hook_rejects_restricted_document_before_any_write(
    tmp_path, capsys, target, config_rel, matcher, skill_rel, state,
):
    from pebra.adapters import enforcement_capability

    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    if state == "malformed":
        entries = [
            agent_init.managed_hook_entry(matcher),
            {"matcher": "Read", "hooks": [{"type": "unknown"}]},
        ]
    else:
        entries = [{
            "matcher": "Read",
            "hooks": [{"type": "command", "command": "pebra gate-hook"}],
        }]
    original = json.dumps({"custom": True, "hooks": {"PreToolUse": entries}}).encode()
    config.write_bytes(original)

    assert _run_with_hook(target, tmp_path) == 2

    assert config.read_bytes() == original
    assert not (tmp_path / skill_rel).exists()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()
    assert not (tmp_path / "AGENTS.md").exists()
    stderr = capsys.readouterr().err
    assert state in stderr
    assert "--check --json" in stderr
    posture = enforcement_capability.probe(
        tmp_path,
        graph_available=True,
        git_available=True,
        hook_runtime_available=True,
        user_hooks_disabled=False,
    )[target]
    assert posture["candidate_bound"] is False
    assert posture["reasons"] == [f"hook_{state}"]


@pytest.mark.parametrize(
    "raw",
    (
        f"user text\n{agent_init._MARK_BEGIN}\nunterminated\n",
        f"{agent_init._MARK_END}\nuser text\n{agent_init._MARK_BEGIN}\n",
        f"{agent_init._MARK_BEGIN}\na\n{agent_init._MARK_END}\n"
        f"{agent_init._MARK_BEGIN}\nb\n{agent_init._MARK_END}\n",
        f"user text\n{agent_init._MARK_END}\n",
        f"{agent_init._MARK_BEGIN}\na\n{agent_init._MARK_BEGIN}\nb\n{agent_init._MARK_END}\n",
    ),
)
def test_codex_rejects_corrupt_managed_markers_without_any_write(tmp_path, raw):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(raw, encoding="utf-8")

    assert _run_with_hook("codex", tmp_path) == 2

    assert agents.read_text(encoding="utf-8") == raw
    assert not (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").exists()
    assert not (tmp_path / ".codex/hooks.json").exists()


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


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _stub_inspection_probes(monkeypatch) -> None:
    from pebra.adapters import enforcement_capability

    monkeypatch.setattr(
        enforcement_capability,
        "probe",
        lambda repo_root, *, graph_available: {
            "claude": {
                "mode": "degraded_fail_open", "candidate_bound": False,
                "reasons": ["graph_unverified_read_only"],
            },
            "codex": {
                "mode": "degraded_fail_open", "candidate_bound": False,
                "reasons": ["graph_unverified_read_only"],
            },
            "mcp": {"mode": "advisory_only", "candidate_bound": False, "reasons": []},
        },
    )


def _check(target: str, root: Path, monkeypatch, *extra: str) -> int:
    _stub_inspection_probes(monkeypatch)
    args = build_parser().parse_args([
        "agent-init", "--target", target, "--repo-root", str(root),
        "--check", "--json", *extra,
    ])
    return args.func(args)


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
@pytest.mark.parametrize("file_state", ("absent", "current", "modified"))
def test_agent_init_check_reports_file_state_without_mutation(
    tmp_path, target, file_state, monkeypatch, capsys,
):
    if file_state != "absent":
        assert _run(target, tmp_path) == 0
        capsys.readouterr()
    if file_state == "modified":
        if target == "claude":
            (tmp_path / _CLAUDE_RULE_REL).write_text("local rule\n", encoding="utf-8")
            (tmp_path / _SKILL_REL).write_text("local skill\n", encoding="utf-8")
        else:
            agents = tmp_path / "AGENTS.md"
            agents.write_text(
                agents.read_text(encoding="utf-8").replace(
                    "## PEBRA safe-edit protocol", "## stale PEBRA protocol"
                ),
                encoding="utf-8",
            )
            (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").write_text(
                "local skill\n", encoding="utf-8"
            )

    before = _tree_snapshot(tmp_path)
    assert _check(target, tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "agent-init"
    assert set(payload) == {
        "command", "target", "protocol_version", "gate_schema_version", "files", "hook",
        "declared_support", "effective_enforcement",
    }
    assert payload["target"] == target
    assert payload["protocol_version"] == 1
    assert payload["gate_schema_version"] == 1
    assert {item["state"] for item in payload["files"]} == {file_state}
    assert payload["declared_support"] == AGENT_HOSTS[target].declared_support
    assert payload["effective_enforcement"]["mode"] == "degraded_fail_open"
    assert payload["effective_enforcement"]["reasons"] == ["graph_unverified_read_only"]
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_agent_init_json_requires_check_without_writing(tmp_path, target, capsys):
    args = build_parser().parse_args([
        "agent-init", "--target", target, "--repo-root", str(tmp_path), "--json",
    ])

    assert args.func(args) == 2
    assert "--json requires --check" in capsys.readouterr().err
    assert _tree_snapshot(tmp_path) == {}


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_agent_init_with_hook_check_is_still_inspection_only(
    tmp_path, target, monkeypatch, capsys,
):
    before = _tree_snapshot(tmp_path)
    assert _check(target, tmp_path, monkeypatch, "--with-hook") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hook"]["state"] == "absent"
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (None, "absent"),
        ({"hooks": {"PreToolUse": [
            agent_init.managed_hook_entry(agent_init._CLAUDE_HOOK_MATCHER)
        ]}}, "exact"),
        ({"hooks": {"PreToolUse": [
            agent_init.managed_hook_entry(agent_init._CLAUDE_HOOK_MATCHER),
            {"matcher": "Read", "hooks": [{"type": "command", "command": "echo ok"}]},
        ]}}, "exact"),
        ({"hooks": {"PreToolUse": [
            agent_init.managed_hook_entry(agent_init._CLAUDE_HOOK_MATCHER),
            {"matcher": "Read", "hooks": [{"type": "command", "command": "pebra gate-hook"}]},
        ]}}, "conflicting"),
        ({"hooks": {"PreToolUse": [
            {"matcher": "Read", "hooks": [{"type": "command", "command": "pebra gate-hook"}]},
        ]}}, "conflicting"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": {}},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": [{}]},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": [
                {"type": "command"}
            ]},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": [
                {"type": "command", "command": 42}
            ]},
        ]}}, "malformed"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": [
                {"type": "command", "command": "pebra gate-hook-v2"}
            ]},
        ]}}, "absent"),
        ({"hooks": {"PreToolUse": [
            {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": [
                {"type": "command", "command": "echo run-my-gate-hook-check"}
            ]},
        ]}}, "absent"),
    ),
)
def test_hook_inspection_state_precedence(tmp_path, raw, expected):
    path = tmp_path / "settings.json"
    if raw is not None:
        path.write_text(json.dumps(raw), encoding="utf-8")
    assert (
        agent_init._inspect_hook_state(
            path, agent_init._CLAUDE_HOOK_MATCHER, host="claude"
        )
        == expected
    )


@pytest.mark.parametrize(
    "raw",
    ("{broken", "null", "[]", '{"hooks": []}', '{"hooks": null}',
     '{"hooks": {"PreToolUse": {}}}', '{"hooks": {"PreToolUse": null}}'),
)
def test_hook_inspection_reports_malformed_document_containers(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(raw, encoding="utf-8")
    assert (
        agent_init._inspect_hook_state(
            path, agent_init._CLAUDE_HOOK_MATCHER, host="claude"
        )
        == "malformed"
    )


@pytest.mark.parametrize("target", ("claude", "codex"))
@pytest.mark.parametrize("hook_state", ("absent", "exact", "conflicting", "malformed"))
def test_agent_init_check_reports_hook_state_without_mutation(
    tmp_path, target, hook_state, monkeypatch, capsys,
):
    matcher = (
        agent_init._CLAUDE_HOOK_MATCHER
        if target == "claude"
        else agent_init._CODEX_HOOK_MATCHER
    )
    path = (
        tmp_path / ".claude/settings.json"
        if target == "claude"
        else tmp_path / ".codex/hooks.json"
    )
    if hook_state != "absent":
        path.parent.mkdir(parents=True)
        if hook_state == "exact":
            raw = {"hooks": {"PreToolUse": [agent_init.managed_hook_entry(matcher)]}}
            path.write_text(json.dumps(raw), encoding="utf-8")
        elif hook_state == "conflicting":
            raw = {
                "hooks": {"PreToolUse": [
                    {"matcher": "wrong", "hooks": [
                        {"type": "command", "command": "pebra gate-hook"}
                    ]}
                ]}
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
        else:
            path.write_text("{broken", encoding="utf-8")
    before = _tree_snapshot(tmp_path)

    assert _check(target, tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hook"]["state"] == hook_state
    assert _tree_snapshot(tmp_path) == before


def test_codex_check_reports_corrupt_managed_markers_modified_without_repair(
    tmp_path, monkeypatch, capsys,
):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(f"user\n{agent_init._MARK_BEGIN}\nunterminated\n", encoding="utf-8")
    before = _tree_snapshot(tmp_path)

    assert _check("codex", tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {item["path"]: item["state"] for item in payload["files"]}
    assert states["AGENTS.md"] == "modified"
    assert _tree_snapshot(tmp_path) == before


def test_codex_check_treats_preserved_crlf_managed_block_as_current(
    tmp_path, monkeypatch, capsys,
):
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"# Project\r\n\r\nUser text.\r\n")
    assert _run("codex", tmp_path) == 0
    capsys.readouterr()
    before = _tree_snapshot(tmp_path)

    assert _check("codex", tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {item["path"]: item["state"] for item in payload["files"]}
    assert states["AGENTS.md"] == "current"
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_check_reports_non_utf8_managed_content_modified_without_mutation(
    tmp_path, target, monkeypatch, capsys,
):
    if target == "claude":
        path = tmp_path / _SKILL_REL
    else:
        path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfeinvalid")
    before = _tree_snapshot(tmp_path)

    assert _check(target, tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    state = {item["path"]: item["state"] for item in payload["files"]}
    assert state[path.relative_to(tmp_path).as_posix()] == "modified"
    assert _tree_snapshot(tmp_path) == before


def test_check_reports_unreadable_file_modified_without_crashing(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / _CLAUDE_RULE_REL
    path.parent.mkdir(parents=True)
    path.write_text("present", encoding="utf-8")
    real_read_bytes = Path.read_bytes

    def unreadable(self):
        if self == path:
            raise OSError("denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert _check("claude", tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    state = {item["path"]: item["state"] for item in payload["files"]}
    assert state[_CLAUDE_RULE_REL.as_posix()] == "modified"


def test_agent_init_check_human_output_uses_same_payload(tmp_path, monkeypatch, capsys):
    _stub_inspection_probes(monkeypatch)
    args = build_parser().parse_args([
        "agent-init", "--target", "claude", "--repo-root", str(tmp_path), "--check",
    ])
    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "configured_enforcing" in output
    assert "effective mode: degraded_fail_open" in output
    assert "hook" in output


def test_agent_init_check_never_probes_codegraph_and_marks_graph_unverified(
    tmp_path, monkeypatch, capsys,
):
    from pebra import composition
    from pebra.adapters import enforcement_capability

    observed: list[bool | None] = []

    def forbidden_probe(*args, **kwargs):
        raise AssertionError("check mode must not invoke CodeGraph")

    def enforcement(repo_root, *, graph_available):
        observed.append(graph_available)
        return {
            "claude": {
                "mode": "degraded_fail_open",
                "candidate_bound": False,
                "reasons": ["graph_unverified_read_only"],
            },
            "codex": {
                "mode": "degraded_fail_open",
                "candidate_bound": False,
                "reasons": ["graph_unverified_read_only"],
            },
        }

    monkeypatch.setattr(composition, "probe_language_capabilities", forbidden_probe)
    monkeypatch.setattr(enforcement_capability, "probe", enforcement)
    args = build_parser().parse_args([
        "agent-init", "--target", "claude", "--repo-root", str(tmp_path),
        "--check", "--json",
    ])

    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert observed == [None]
    assert payload["effective_enforcement"]["reasons"] == ["graph_unverified_read_only"]


@pytest.mark.parametrize(
    "sibling",
    (
        42,
        {"matcher": "Read", "hooks": [42]},
        {"matcher": agent_init._CLAUDE_HOOK_MATCHER, "hooks": {}},
    ),
)
def test_hook_inspection_malformed_sibling_overrides_exact(tmp_path, sibling):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            agent_init.managed_hook_entry(agent_init._CLAUDE_HOOK_MATCHER),
            sibling,
        ]},
    }), encoding="utf-8")

    assert (
        agent_init._inspect_hook_state(
            path, agent_init._CLAUDE_HOOK_MATCHER, host="claude"
        )
        == "malformed"
    )


def _directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")


def _file_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")


def _hardlink_or_skip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")


@pytest.mark.parametrize(
    ("target", "hook_rel", "skill_rel"),
    (
        ("claude", ".claude/settings.json", ".claude/skills/pebra-safe-edit/SKILL.md"),
        ("codex", ".codex/hooks.json", ".agents/skills/pebra-safe-edit/SKILL.md"),
    ),
)
def test_agent_init_rejects_hardlinked_hook_without_external_or_partial_write(
    tmp_path, capsys, target, hook_rel, skill_rel,
):
    matcher = (
        agent_init._CLAUDE_HOOK_MATCHER
        if target == "claude"
        else agent_init._CODEX_HOOK_MATCHER
    )
    outside = tmp_path.parent / f"{tmp_path.name}-{target}-outside-hook.json"
    original = (json.dumps({
        "hooks": {"PreToolUse": [agent_init.managed_hook_entry(matcher)]},
    }) + "\n").encode()
    outside.write_bytes(original)
    _hardlink_or_skip(outside, tmp_path / hook_rel)

    assert _run_with_hook(target, tmp_path) == 2

    assert "hardlink" in capsys.readouterr().err.lower()
    assert outside.read_bytes() == original
    assert not (tmp_path / skill_rel).exists()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.parametrize(
    ("target", "instruction_rel", "hook_rel"),
    (
        ("claude", ".claude/rules/pebra-safe-edit.md", ".claude/settings.json"),
        ("codex", "AGENTS.md", ".codex/hooks.json"),
    ),
)
def test_check_reports_hardlinked_managed_files_conservatively(
    tmp_path, monkeypatch, capsys, target, instruction_rel, hook_rel,
):
    matcher = (
        agent_init._CLAUDE_HOOK_MATCHER
        if target == "claude"
        else agent_init._CODEX_HOOK_MATCHER
    )
    instruction_content = (
        agent_init._CLAUDE_RULE_MD
        if target == "claude"
        else agent_init._render_agents_md(tmp_path).content
    )
    outside_instruction = tmp_path.parent / f"{tmp_path.name}-{target}-instruction"
    outside_instruction.write_text(instruction_content, encoding="utf-8", newline="")
    _hardlink_or_skip(outside_instruction, tmp_path / instruction_rel)
    outside_hook = tmp_path.parent / f"{tmp_path.name}-{target}-hook"
    outside_hook.write_text(json.dumps({
        "hooks": {"PreToolUse": [agent_init.managed_hook_entry(matcher)]},
    }), encoding="utf-8")
    _hardlink_or_skip(outside_hook, tmp_path / hook_rel)
    before_instruction = outside_instruction.read_bytes()
    before_hook = outside_hook.read_bytes()

    assert _check(target, tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)

    states = {item["path"]: item["state"] for item in payload["files"]}
    assert states[instruction_rel] == "modified"
    assert payload["hook"]["state"] == "conflicting"
    assert payload["effective_enforcement"]["candidate_bound"] is False
    assert outside_instruction.read_bytes() == before_instruction
    assert outside_hook.read_bytes() == before_hook


@pytest.mark.parametrize(
    ("target", "parent_rel", "unexpected_rel"),
    (
        ("claude", ".claude/skills", "pebra-safe-edit/SKILL.md"),
        ("codex", ".agents/skills", "pebra-safe-edit/SKILL.md"),
    ),
)
def test_agent_init_rejects_managed_parent_redirect_before_any_write(
    tmp_path, target, parent_rel, unexpected_rel, capsys,
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-{target}"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"outside stays unchanged")
    parent = tmp_path / parent_rel
    parent.parent.mkdir(parents=True)
    _directory_symlink_or_skip(parent, outside)
    before = _tree_snapshot(outside)

    assert _run(target, tmp_path) == 2

    assert "redirect" in capsys.readouterr().err.lower()
    assert _tree_snapshot(outside) == before
    assert not (outside / unexpected_rel).exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()


def test_agent_init_rejects_broken_managed_file_symlink(tmp_path, capsys):
    skill = tmp_path / _SKILL_REL
    skill.parent.mkdir(parents=True)
    _file_symlink_or_skip(skill, tmp_path / "missing-target")

    assert _run("claude", tmp_path) == 2

    assert skill.is_symlink()
    assert "redirect" in capsys.readouterr().err.lower()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()


@pytest.mark.parametrize(
    ("target", "obstruction_rel", "earlier_rel"),
    (
        ("claude", ".claude/skills/pebra-safe-edit/SKILL.md", _CLAUDE_RULE_REL),
        ("codex", ".agents/skills/pebra-safe-edit/SKILL.md", "AGENTS.md"),
    ),
)
def test_agent_init_rejects_directory_destination_before_any_write(
    tmp_path, capsys, target, obstruction_rel, earlier_rel,
):
    obstruction = tmp_path / obstruction_rel
    obstruction.mkdir(parents=True)

    assert _run(target, tmp_path) == 2

    assert "regular file" in capsys.readouterr().err.lower()
    assert obstruction.is_dir()
    assert not (tmp_path / earlier_rel).exists()


@pytest.mark.parametrize(
    ("target", "parent_rel", "earlier_rel"),
    (
        ("claude", ".claude/skills", _CLAUDE_RULE_REL),
        ("codex", ".agents/skills", "AGENTS.md"),
    ),
)
def test_agent_init_rejects_regular_file_parent_before_any_write(
    tmp_path, capsys, target, parent_rel, earlier_rel,
):
    parent = tmp_path / parent_rel
    parent.parent.mkdir(parents=True)
    parent.write_bytes(b"not a directory")

    assert _run(target, tmp_path) == 2

    assert "directory" in capsys.readouterr().err.lower()
    assert parent.read_bytes() == b"not a directory"
    assert not (tmp_path / earlier_rel).exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation unavailable")
def test_agent_init_rejects_fifo_destination_before_any_write(tmp_path, capsys):
    skill = tmp_path / _SKILL_REL
    skill.parent.mkdir(parents=True)
    os.mkfifo(skill)

    assert _run("claude", tmp_path) == 2

    assert "regular file" in capsys.readouterr().err.lower()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()


@pytest.mark.parametrize("special_mode", (stat.S_IFIFO, stat.S_IFCHR), ids=("fifo", "device"))
def test_agent_init_rejects_special_destination_types_before_any_write(
    tmp_path, monkeypatch, special_mode,
):
    skill = tmp_path / _SKILL_REL
    skill.parent.mkdir(parents=True)
    original_lstat = Path.lstat

    def _lstat(path):
        if path == skill:
            return SimpleNamespace(st_mode=special_mode)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", _lstat)

    with pytest.raises(agent_init.AgentInitConfigError, match="regular file"):
        agent_init._reject_invalid_managed_path_types(tmp_path, skill)

    assert not (tmp_path / _CLAUDE_RULE_REL).exists()


def test_agent_init_metadata_error_is_fail_closed_before_any_write(
    tmp_path, monkeypatch, capsys,
):
    original_lstat = Path.lstat
    unreadable = tmp_path / ".claude"

    def _lstat(path):
        if path == unreadable:
            raise OSError("metadata unavailable")
        return original_lstat(path)

    monkeypatch.setattr(agent_init, "_unsafe_managed_path", lambda root, path: None)
    monkeypatch.setattr(Path, "lstat", _lstat)

    assert _run("claude", tmp_path) == 2

    assert "inspect" in capsys.readouterr().err.lower()
    assert not (tmp_path / _CLAUDE_RULE_REL).exists()


def test_check_reports_redirected_instruction_and_hook_without_reading_targets(
    tmp_path, monkeypatch, capsys,
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-check"
    outside.mkdir()
    outside_agents = outside / "AGENTS.md"
    outside_agents.write_bytes(b"outside agents")
    outside_hook = outside / "hooks.json"
    outside_hook.write_bytes(b'{"hooks": {"PreToolUse": []}}')
    _file_symlink_or_skip(tmp_path / "AGENTS.md", outside_agents)
    codex_parent = tmp_path / ".codex"
    _directory_symlink_or_skip(codex_parent, outside)
    before = _tree_snapshot(outside)

    assert _check("codex", tmp_path, monkeypatch) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {item["path"]: item["state"] for item in payload["files"]}
    assert states["AGENTS.md"] == "modified"
    assert payload["hook"]["state"] == "conflicting"
    assert _tree_snapshot(outside) == before


def test_agent_init_accepts_repo_root_reached_through_symlink(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir()
    alias = tmp_path / "alias"
    _directory_symlink_or_skip(alias, real_root)

    assert _run("claude", alias) == 0
    assert (real_root / _CLAUDE_RULE_REL).is_file()


def test_is_redirect_honors_junction_api_when_available(tmp_path, monkeypatch):
    from pebra.adapters import path_safety

    if not hasattr(Path, "is_junction"):
        pytest.skip("Path.is_junction unavailable")
    candidate = tmp_path / "junction"
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_junction", lambda self: self == candidate)

    assert path_safety.is_redirect(candidate) is True


def test_is_redirect_fails_closed_when_metadata_inspection_errors(tmp_path, monkeypatch):
    from pebra.adapters import path_safety

    candidate = tmp_path / "unreadable"

    def raise_os_error(self):
        raise OSError("metadata unavailable")

    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    if hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(Path, "lstat", raise_os_error)

    assert path_safety.is_redirect(candidate) is True


def test_is_redirect_allows_missing_path(tmp_path):
    from pebra.adapters import path_safety

    assert path_safety.is_redirect(tmp_path / "missing") is False


def test_is_redirect_does_not_misclassify_not_a_directory_as_redirect(
    tmp_path, monkeypatch,
):
    from pebra.adapters import path_safety

    candidate = tmp_path / "regular-parent" / "child"
    original_lstat = Path.lstat

    def raise_not_a_directory(self):
        if self == candidate:
            raise NotADirectoryError("parent is not a directory")
        return original_lstat(self)

    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    if hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(Path, "lstat", raise_not_a_directory)

    assert path_safety.is_redirect(candidate) is False


def test_redirected_component_treats_non_descendant_as_unsafe(tmp_path):
    from pebra.adapters import path_safety

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"

    assert path_safety.redirected_component(root, outside) == outside


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_agent_init_rejects_windows_junction_parent_without_external_write(
    tmp_path, capsys,
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-junction"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"outside stays unchanged")
    link = tmp_path / ".claude" / "skills"
    link.parent.mkdir(parents=True)
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, text=True, check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"junction creation unavailable: {made.stderr}")
    before = _tree_snapshot(outside)
    try:
        assert _run("claude", tmp_path) == 2
        assert "redirect" in capsys.readouterr().err.lower()
        assert _tree_snapshot(outside) == before
    finally:
        if link.exists():
            link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_codex_hook_junction_is_rejected_or_reported_without_external_read(
    tmp_path, capsys,
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-hook-junction"
    outside.mkdir()
    hook = outside / "hooks.json"
    hook.write_bytes(b'{"hooks": {"PreToolUse": []}}')
    link = tmp_path / ".codex"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, text=True, check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"junction creation unavailable: {made.stderr}")
    before = _tree_snapshot(outside)
    try:
        assert _run_with_hook("codex", tmp_path) == 2
        assert "redirect" in capsys.readouterr().err.lower()
        assert not (tmp_path / "AGENTS.md").exists()
        assert _tree_snapshot(outside) == before

        args = build_parser().parse_args([
            "agent-init", "--target", "codex", "--repo-root", str(tmp_path),
            "--check", "--json",
        ])
        assert args.func(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["hook"]["state"] == "conflicting"
        assert payload["effective_enforcement"]["candidate_bound"] is False
        assert "hook_conflicting" in payload["effective_enforcement"]["reasons"]
        assert _tree_snapshot(outside) == before
    finally:
        if link.exists():
            link.rmdir()
