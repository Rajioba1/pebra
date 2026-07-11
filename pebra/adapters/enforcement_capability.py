"""Measured enforcement posture for supported coding-agent hosts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _hook_installed(path: Path, expected_matcher: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    entries = hooks.get("PreToolUse")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(entry, dict)
        and entry.get("matcher") == expected_matcher
        and isinstance(entry.get("hooks"), list)
        and any(
            isinstance(hook, dict)
            and hook.get("type") == "command"
            and hook.get("command") == "pebra gate-hook"
            for hook in entry.get("hooks", [])
        )
        for entry in entries
    )


def _git_available(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _configured_host(
    *, installed: bool, supported_mode: str, graph_available: bool, git_available: bool
) -> dict[str, Any]:
    if not installed:
        return {
            "mode": "advisory_only",
            "candidate_bound": False,
            "reasons": ["pre-edit hook not installed"],
        }
    reasons: list[str] = []
    if not graph_available:
        reasons.append("graph")
    if not git_available:
        reasons.append("git_head")
    return {
        "mode": "degraded_fail_open" if reasons else supported_mode,
        "candidate_bound": not reasons,
        "reasons": reasons,
    }


def probe(
    repo_root: str | Path,
    *,
    graph_available: bool,
    git_available: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Report current repo-local host posture; never claim host loading we cannot verify."""
    root = Path(repo_root).resolve()
    git_ok = _git_available(root) if git_available is None else git_available
    return {
        "claude": _configured_host(
            installed=_hook_installed(root / ".claude" / "settings.json", "Edit|Write|MultiEdit"),
            supported_mode="verified_enforcing",
            graph_available=graph_available,
            git_available=git_ok,
        ),
        "codex": _configured_host(
            installed=_hook_installed(root / ".codex" / "hooks.json", "apply_patch"),
            supported_mode="best_effort",
            graph_available=graph_available,
            git_available=git_ok,
        ),
        "mcp": {
            "mode": "advisory_only",
            "candidate_bound": False,
            "reasons": ["MCP tools do not intercept host writes"],
        },
    }
