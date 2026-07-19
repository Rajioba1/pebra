"""Enforcement capability reports host guarantees separately from graph-language coverage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pebra.adapters import enforcement_capability
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


def _hook(root: Path, rel: str, *, matcher: str = "Edit|Write|MultiEdit", command: str = "pebra gate-hook") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        }]},
    }), encoding="utf-8")


def test_unconfigured_hosts_are_reported_as_advisory_only(tmp_path: Path) -> None:
    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "advisory_only"
    assert result["codex"]["mode"] == "advisory_only"
    assert result["mcp"]["mode"] == "advisory_only"


def test_claude_hook_is_configured_enforcing_when_prerequisites_are_live(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "configured_enforcing"
    assert result["claude"]["candidate_bound"] is True


def test_installed_hook_reports_degraded_fail_open_without_graph(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(
        tmp_path, graph_available=False, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert result["claude"]["candidate_bound"] is False
    assert "graph" in result["claude"]["reasons"]


def test_codex_hook_is_best_effort_not_verified(tmp_path: Path) -> None:
    _hook(tmp_path, ".codex/hooks.json", matcher="apply_patch")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["codex"]["mode"] == "best_effort"
    assert result["codex"]["candidate_bound"] is True


def test_suggestive_command_or_wrong_matcher_does_not_claim_enforcement(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json", matcher="Read", command="echo pebra gate-hook")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "advisory_only"
    assert result["claude"]["candidate_bound"] is False


def test_hook_probe_rejects_lookalike_command(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
        }]},
    }), encoding="utf-8")

    assert enforcement_capability._hook_installed(settings, "Edit|Write|MultiEdit") is False


def test_malformed_valid_json_fails_safe_to_advisory_only(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"hooks": ["bad"]}', encoding="utf-8")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "advisory_only"


def test_installed_hook_degrades_when_pebra_command_is_not_runnable(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=False,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert "gate_hook_runtime" in result["claude"]["reasons"]


def test_conflicting_claude_local_hook_config_degrades_posture(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")
    _hook(tmp_path, ".claude/settings.local.json", matcher="Read", command="echo local")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert "local_hook_override" in result["claude"]["reasons"]


def test_installed_hook_degrades_without_git_head(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=False, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert "git_head" in result["claude"]["reasons"]


def test_hook_runtime_probe_requires_matching_candidate_binding_handshake(monkeypatch) -> None:
    monkeypatch.setattr(enforcement_capability.shutil, "which", lambda name: None)
    assert enforcement_capability._hook_runtime_available() is False

    monkeypatch.setattr(enforcement_capability.shutil, "which", lambda name: "/bin/pebra")
    monkeypatch.setattr(
        enforcement_capability.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0,
            stdout=json.dumps({
                "candidate_binding_protocol": CANDIDATE_BINDING_ALGORITHM,
                "complete_candidate_event_required": True,
            }),
        ),
    )
    assert enforcement_capability._hook_runtime_available() is True

    monkeypatch.setattr(
        enforcement_capability.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="{}"),
    )
    assert enforcement_capability._hook_runtime_available() is False


def test_malformed_local_hook_config_degrades_configured_posture(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")
    local = tmp_path / ".claude" / "settings.local.json"
    local.write_text("{broken", encoding="utf-8")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert "local_hook_override" in result["claude"]["reasons"]


def test_disabled_claude_hooks_do_not_claim_enforcement(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")
    path = tmp_path / ".claude" / "settings.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["disableAllHooks"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=False,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert result["claude"]["candidate_bound"] is False
    assert "hooks_disabled" in result["claude"]["reasons"]


def test_user_level_disable_all_hooks_degrades_project_posture(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(
        tmp_path, graph_available=True, git_available=True, hook_runtime_available=True,
        user_hooks_disabled=True,
    )

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert result["claude"]["candidate_bound"] is False
    assert "hooks_disabled" in result["claude"]["reasons"]
