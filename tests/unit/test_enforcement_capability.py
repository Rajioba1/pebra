"""Enforcement capability reports host guarantees separately from graph-language coverage."""

from __future__ import annotations

import json
from pathlib import Path

from pebra.adapters import enforcement_capability


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


def test_claude_hook_is_verified_enforcing_when_prerequisites_are_live(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "verified_enforcing"
    assert result["claude"]["candidate_bound"] is True


def test_installed_hook_reports_degraded_fail_open_without_graph(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json")

    result = enforcement_capability.probe(tmp_path, graph_available=False, git_available=True)

    assert result["claude"]["mode"] == "degraded_fail_open"
    assert result["claude"]["candidate_bound"] is False
    assert "graph" in result["claude"]["reasons"]


def test_codex_hook_is_best_effort_not_verified(tmp_path: Path) -> None:
    _hook(tmp_path, ".codex/hooks.json", matcher="apply_patch")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["codex"]["mode"] == "best_effort"
    assert result["codex"]["candidate_bound"] is True


def test_suggestive_command_or_wrong_matcher_does_not_claim_enforcement(tmp_path: Path) -> None:
    _hook(tmp_path, ".claude/settings.json", matcher="Read", command="echo pebra gate-hook")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "advisory_only"
    assert result["claude"]["candidate_bound"] is False


def test_malformed_valid_json_fails_safe_to_advisory_only(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"hooks": ["bad"]}', encoding="utf-8")

    result = enforcement_capability.probe(tmp_path, graph_available=True, git_available=True)

    assert result["claude"]["mode"] == "advisory_only"
