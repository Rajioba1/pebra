"""Pure structural contract for PEBRA-owned agent-host hooks."""

from __future__ import annotations

from typing import Any, Final

# Installed compatibility contract. A change requires an explicit legacy-signature migration.
HOOK_COMMAND: Final[str] = "pebra gate-hook"


def managed_hook_entry(matcher: str) -> dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    }


def is_managed_hook_entry(value: object, matcher: str) -> bool:
    return value == managed_hook_entry(matcher)
