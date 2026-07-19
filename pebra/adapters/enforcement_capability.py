"""Observed enforcement configuration posture for supported coding-agent hosts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pebra.core.agent_hook_contract import is_managed_hook_entry
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


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
    return any(is_managed_hook_entry(entry, expected_matcher) for entry in entries)


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


def _hook_runtime_available() -> bool:
    """Confirm the PATH executable implements the candidate-binding contract we report."""
    executable = shutil.which("pebra")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "gate-hook", "--capabilities"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return False
    return (
        payload.get("candidate_binding_protocol") == CANDIDATE_BINDING_ALGORITHM
        and payload.get("complete_candidate_event_required") is True
    )


def _hooks_disabled(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("disableAllHooks") is True


def _local_hook_conflicts(path: Path, expected_matcher: str) -> bool:
    """Conservatively flag a local PreToolUse configuration we cannot prove keeps this hook."""
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = ((payload.get("hooks") or {}).get("PreToolUse"))
    except (OSError, ValueError, AttributeError):
        return True
    if entries is None:
        return False
    return not _hook_installed(path, expected_matcher)


def _configured_host(
    *, installed: bool, supported_mode: str, graph_available: bool, git_available: bool,
    hook_runtime_available: bool, local_hook_conflict: bool = False, hooks_disabled: bool = False,
) -> dict[str, Any]:
    if not installed:
        return {
            "mode": "advisory_only",
            "candidate_bound": False,
            "reasons": ["pre-edit hook not installed"],
        }
    reasons: list[str] = []
    if not hook_runtime_available:
        reasons.append("gate_hook_runtime")
    if not graph_available:
        reasons.append("graph")
    if not git_available:
        reasons.append("git_head")
    if local_hook_conflict:
        reasons.append("local_hook_override")
    if hooks_disabled:
        reasons.append("hooks_disabled")
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
    hook_runtime_available: bool | None = None,
    user_hooks_disabled: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Report current repo-local host posture; never claim host loading we cannot verify."""
    root = Path(repo_root).resolve()
    git_ok = _git_available(root) if git_available is None else git_available
    claude_installed = _hook_installed(root / ".claude" / "settings.json", "Edit|Write|MultiEdit")
    codex_installed = _hook_installed(root / ".codex" / "hooks.json", "apply_patch")
    runtime_ok = (
        _hook_runtime_available()
        if hook_runtime_available is None and (claude_installed or codex_installed)
        else bool(hook_runtime_available)
    )
    user_disabled = (
        _hooks_disabled(Path.home() / ".claude" / "settings.json")
        if user_hooks_disabled is None
        else user_hooks_disabled
    )
    return {
        "claude": _configured_host(
            installed=claude_installed,
            supported_mode="configured_enforcing",
            graph_available=graph_available,
            git_available=git_ok,
            hook_runtime_available=runtime_ok,
            local_hook_conflict=_local_hook_conflicts(
                root / ".claude" / "settings.local.json", "Edit|Write|MultiEdit"
            ),
            hooks_disabled=(
                _hooks_disabled(root / ".claude" / "settings.json")
                or _hooks_disabled(root / ".claude" / "settings.local.json")
                or user_disabled
            ),
        ),
        "codex": _configured_host(
            installed=codex_installed,
            supported_mode="best_effort",
            graph_available=graph_available,
            git_available=git_ok,
            hook_runtime_available=runtime_ok,
        ),
        "mcp": {
            "mode": "advisory_only",
            "candidate_bound": False,
            "reasons": ["MCP tools do not intercept host writes"],
        },
    }
