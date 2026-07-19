"""Pure structural contract for PEBRA-owned agent-host hooks."""

from __future__ import annotations

from typing import Any, Final, Literal

# Installed compatibility contract. A change requires an explicit legacy-signature migration.
HOOK_COMMAND: Final[str] = "pebra gate-hook"


def managed_hook_entry(matcher: str) -> dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    }


def is_managed_hook_entry(value: object, matcher: str) -> bool:
    return value == managed_hook_entry(matcher)


HookState = Literal["absent", "exact", "conflicting", "malformed"]
HookHost = Literal["claude", "codex"]


def _nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_hook_handler(value: object, *, host: HookHost) -> bool:
    if not isinstance(value, dict):
        return False
    hook_type = value.get("type")
    if not _nonblank_string(hook_type):
        return False
    if hook_type == "command":
        return _nonblank_string(value.get("command"))
    if hook_type in {"prompt", "agent"}:
        return _nonblank_string(value.get("prompt"))
    if host == "claude" and hook_type == "http":
        return _nonblank_string(value.get("url"))
    if host == "claude" and hook_type == "mcp_tool":
        return _nonblank_string(value.get("server")) and _nonblank_string(
            value.get("tool")
        )
    return False


def classify_hook_document(
    document: object, matcher: str, *, host: HookHost
) -> HookState:
    """Classify a decoded host hook document without granting mutation ownership."""
    if not isinstance(document, dict):
        return "malformed"
    if "hooks" not in document:
        return "absent"
    hooks = document["hooks"]
    if not isinstance(hooks, dict):
        return "malformed"
    if "PreToolUse" not in hooks:
        return "absent"
    entries = hooks["PreToolUse"]
    if not isinstance(entries, list):
        return "malformed"

    exact = False
    conflicting = False
    malformed = False
    for entry in entries:
        if not isinstance(entry, dict):
            malformed = True
            continue
        entry_matcher = entry.get("matcher", "")
        if not isinstance(entry_matcher, str):
            malformed = True
            continue
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            if entry_matcher == matcher:
                conflicting = True
            else:
                malformed = True
            continue
        if not handlers:
            malformed = True
            continue
        if any(not _valid_hook_handler(handler, host=host) for handler in handlers):
            malformed = True
            continue
        if is_managed_hook_entry(entry, matcher):
            exact = True
        elif any(handler.get("command") == HOOK_COMMAND for handler in handlers):
            conflicting = True

    if malformed:
        return "malformed"
    if conflicting:
        return "conflicting"
    return "exact" if exact else "absent"
