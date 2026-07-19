"""Observed enforcement configuration posture for supported coding-agent hosts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pebra.adapters.path_safety import unsafe_managed_path
from pebra.core.agent_hook_contract import classify_hook_document
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


def _hook_state(
    path: Path,
    expected_matcher: str,
    *,
    host: Literal["claude", "codex"],
    root: Path | None = None,
) -> str:
    if root is not None and unsafe_managed_path(root, path) is not None:
        return "conflicting"
    if not path.exists():
        return "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "malformed"
    return classify_hook_document(payload, expected_matcher, host=host)


def _hook_installed(
    path: Path, expected_matcher: str, *, host: Literal["claude", "codex"]
) -> bool:
    return _hook_state(path, expected_matcher, host=host) == "exact"


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
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("candidate_binding_protocol") == CANDIDATE_BINDING_ALGORITHM
        and payload.get("complete_candidate_event_required") is True
    )


def _hooks_disabled(path: Path, *, root: Path | None = None) -> bool:
    if root is not None and unsafe_managed_path(root, path) is not None:
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("disableAllHooks") is True


def _local_hook_conflicts(
    path: Path,
    expected_matcher: str,
    *,
    host: Literal["claude", "codex"],
    root: Path,
) -> bool:
    """Conservatively flag a local PreToolUse configuration we cannot prove keeps this hook."""
    if unsafe_managed_path(root, path) is not None:
        return True
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(payload, dict):
        return True
    if "hooks" not in payload:
        return False
    hooks = payload["hooks"]
    if not isinstance(hooks, dict):
        return True
    if "PreToolUse" not in hooks:
        return False
    return classify_hook_document(payload, expected_matcher, host=host) != "exact"


def _configured_host(
    *, hook_state: str, supported_mode: str, graph_available: bool | None, git_available: bool,
    hook_runtime_available: bool, local_hook_conflict: bool = False, hooks_disabled: bool = False,
) -> dict[str, Any]:
    if hook_state in {"conflicting", "malformed"}:
        return {
            "mode": "degraded_fail_open",
            "candidate_bound": False,
            "reasons": [f"hook_{hook_state}"],
        }
    if hook_state != "exact":
        return {
            "mode": "advisory_only",
            "candidate_bound": False,
            "reasons": ["pre-edit hook not installed"],
        }
    reasons: list[str] = []
    if not hook_runtime_available:
        reasons.append("gate_hook_runtime")
    if graph_available is None:
        reasons.append("graph_unverified_read_only")
    elif not graph_available:
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
    graph_available: bool | None,
    git_available: bool | None = None,
    hook_runtime_available: bool | None = None,
    user_hooks_disabled: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Report current repo-local host posture; never claim host loading we cannot verify."""
    root = Path(repo_root).resolve()
    git_ok = _git_available(root) if git_available is None else git_available
    claude_state = _hook_state(
        root / ".claude" / "settings.json",
        "Edit|Write|MultiEdit",
        host="claude",
        root=root,
    )
    codex_state = _hook_state(
        root / ".codex" / "hooks.json", "apply_patch", host="codex", root=root
    )
    claude_installed = claude_state == "exact"
    codex_installed = codex_state == "exact"
    runtime_ok = (
        _hook_runtime_available()
        if hook_runtime_available is None and (claude_installed or codex_installed)
        else bool(hook_runtime_available)
    )
    home = Path.home().resolve()
    user_disabled = (
        _hooks_disabled(
            home / ".claude" / "settings.json", root=home
        )
        if user_hooks_disabled is None
        else user_hooks_disabled
    )
    return {
        "claude": _configured_host(
            hook_state=claude_state,
            supported_mode="configured_enforcing",
            graph_available=graph_available,
            git_available=git_ok,
            hook_runtime_available=runtime_ok,
            local_hook_conflict=_local_hook_conflicts(
                root / ".claude" / "settings.local.json",
                "Edit|Write|MultiEdit",
                host="claude",
                root=root,
            ),
            hooks_disabled=(
                _hooks_disabled(root / ".claude" / "settings.json", root=root)
                or _hooks_disabled(root / ".claude" / "settings.local.json", root=root)
                or user_disabled
            ),
        ),
        "codex": _configured_host(
            hook_state=codex_state,
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
