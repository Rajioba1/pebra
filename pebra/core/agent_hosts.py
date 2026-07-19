"""Minimal stable facts for supported coding-agent hosts."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True)
class HostSpec:
    skill_path: str
    instruction_paths: tuple[str, ...]
    hook_path: str
    hook_matcher: str
    declared_support: str


AGENT_HOSTS: Final[Mapping[str, HostSpec]] = MappingProxyType({
    "claude": HostSpec(
        skill_path=".claude/skills/pebra-safe-edit/SKILL.md",
        instruction_paths=(".claude/rules/pebra-safe-edit.md",),
        hook_path=".claude/settings.json",
        hook_matcher="Edit|Write|MultiEdit",
        declared_support="configured_enforcing",
    ),
    "codex": HostSpec(
        skill_path=".agents/skills/pebra-safe-edit/SKILL.md",
        instruction_paths=("AGENTS.md",),
        hook_path=".codex/hooks.json",
        hook_matcher="apply_patch",
        declared_support="best_effort",
    ),
})
