"""`pebra agent-init` — scaffold the PEBRA safe-edit protocol for a coding-agent host.

By default this writes the passive ``pebra-safe-edit`` skill and host rules that tell an agent to
CONSULT PEBRA before edits (assess -> read risk -> edit -> verify -> record). With ``--with-hook`` it
also writes a host hook config that calls ``pebra gate-hook`` before structured edits. Claude's
``.claude/settings.json`` hook is the supported enforcement surface; Codex's repo-local
``.codex/hooks.json`` is best-effort because Codex hook loading differs by host/plugin install.
Templates are inline string constants so nothing depends on package-data being shipped.

Targets:
- ``claude`` -> ``.claude/skills/pebra-safe-edit/SKILL.md``
- ``codex``  -> ``AGENTS.md`` (idempotent managed block; the reliable Codex surface) plus
  ``.agents/skills/pebra-safe-edit/SKILL.md`` (the documented repo-local Codex skills path).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pebra.core.agent_hook_contract import is_managed_hook_entry, managed_hook_entry

_SKILL_DIR = "pebra-safe-edit"
_CLAUDE_HOOK_MATCHER = "Edit|Write|MultiEdit"
_CODEX_HOOK_MATCHER = "apply_patch"
_MARK_BEGIN = "<!-- BEGIN pebra-safe-edit (managed by `pebra agent-init`) -->"
_MARK_END = "<!-- END pebra-safe-edit -->"

# The protocol body — shared by the SKILL.md (with frontmatter) and the AGENTS.md managed block.
# Must-consult wording only; no enforcement claim in Phase 1.
_PROTOCOL_BODY = """\
Before any significant edit, rename, or delete, you must consult PEBRA first. This is a pre-edit
obligation, not optional. Do not skip these steps:

1. **Assess (pre-edit).** Draft the intended change, then run
   `pebra assess <request.json> --json` with the target file(s) in `expected_files` and the intended
   unified diff in `proposed_patch`. Read the returned decision, `scores.expected_loss`, safe edit
   scope, and required checks before touching the code.
2. **Revise when asked.** If the decision is `revise_safer`, do not apply the original patch. Use
   `model_guidance_packet.advisory.safer_route` (its `summary` and `constraints`) to draft a safer or
   compatibility-preserving candidate. For public contract changes, consider retaining the existing
   entry point through an alias, wrapper, adapter, default implementation, or deprecation bridge.
   Then resubmit by running `pebra assess` again with that new `proposed_patch`. Keep
   the same task text and stable action ID across these revision requests, even when the safer route
   moves to a different file, so the bounded revision lineage cannot reset. Keep reducing risk and resubmitting
   until the reassessment permits editing and shows lower risk (for example, lower
   `scores.expected_loss` or a less severe decision). PEBRA does not accept
   self-reported candidate verification in the request, so revise the change until the risk drops on
   its own; if it will not, escalate as in step 3.
3. **Escalate when asked.** Treat the assess JSON `next_action` as authoritative. If the decision is
   `ask_human`, present its reason, risk/benefit values, remaining uncertainty, required controls, and
   exact candidate to the user. Launch `pebra accept-risk --apply`; a trusted human or host operator must review the
   displayed risk/benefit evidence and type the approval in its interactive terminal. Do not answer the
   approval prompt yourself. Do not create or claim the sanction yourself. PEBRA creates the bound
   sanction, then will reassess the exact candidate
   and apply it only after a
   `proceed` with `risk_mode=controlled_high_risk`. If the decision is `reject`, stop and ask for a different route.
   Never treat either decision as permission to edit.
4. **Edit.** When `next_action.type` is `apply_exact_candidate_then_verify`, run its returned command
   (`pebra apply-candidate --assessment-id <returned-id>`). Do not manually retype or reconstruct the
   patch. Keep all later edits within the safe scope PEBRA reports.
5. **Verify.** After editing, run `pebra verify --assessment-id <id> --scope staged` and resolve any
   scope drift or build failures it reports.
6. **Record.** Run `pebra record-outcome --assessment-id <id> --status completed`.

Consulting PEBRA before editing high-impact code is how you avoid breaking dependents you cannot see
from local context.
"""

_SKILL_MD = f"""\
---
name: pebra-safe-edit
description: Use BEFORE editing, renaming, or deleting any function, class, or file. Consult PEBRA's pre-edit risk assessment first to avoid breaking dependents.
---

# PEBRA safe edit

{_PROTOCOL_BODY}"""

_AGENTS_HEADING = "## PEBRA safe-edit protocol"


@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    content: str


class AgentInitConfigError(ValueError):
    pass


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "agent-init",
        help="Scaffold the pebra-safe-edit skill/rules and optionally a host pre-edit gate hook.",
    )
    p.add_argument(
        "--target", choices=("claude", "codex"), required=True,
        help="Agent host whose PEBRA protocol files should be installed.",
    )
    p.add_argument(
        "--repo-root", default=".",
        help="Repository path (defaults to current directory).",
    )
    p.add_argument("--with-hook", action="store_true",
                   help="Also install a pre-edit gate hook config (Claude verified; Codex repo-local "
                        ".codex/hooks.json is best-effort and host-dependent). Default: instructions only.")
    p.set_defaults(func=run_agent_init)


def run_agent_init(args: Any) -> int:
    repo_root = Path(args.repo_root)
    with_hook = getattr(args, "with_hook", False)
    try:
        planned = _plan_agent_init(repo_root, args.target, with_hook)
    except AgentInitConfigError as exc:
        print(f"agent-init: {exc}", file=sys.stderr)
        return 2
    for write in planned:
        write.path.parent.mkdir(parents=True, exist_ok=True)
        write.path.write_text(write.content, encoding="utf-8")
        print(f"wrote {write.path}")
    if args.target == "codex":
        print("note: AGENTS.md is the reliable Codex surface; .agents/skills is best-effort per Codex docs.")
    if with_hook:
        if args.target == "codex":
            print("installed best-effort Codex hook config; verify your Codex host loads .codex/hooks.json.")
        else:
            print("installed the enforcing PreToolUse gate hook (pebra gate-hook) for claude.")
    else:
        print("instruction-only: no enforcement hook installed (pass --with-hook to enable).")
    return 0


def _plan_agent_init(repo_root: Path, target: str, with_hook: bool) -> list[PlannedWrite]:
    if target == "claude":
        planned = [_render_skill(repo_root, ".claude")]
        if with_hook:
            path = repo_root / ".claude" / "settings.json"
            planned.append(PlannedWrite(path, _render_hook_config(path, _CLAUDE_HOOK_MATCHER)))
        return planned

    planned = [_render_agents_md(repo_root), _render_skill(repo_root, ".agents")]
    if with_hook:
        path = repo_root / ".codex" / "hooks.json"
        planned.append(PlannedWrite(path, _render_hook_config(path, _CODEX_HOOK_MATCHER)))
    return planned


def _render_skill(repo_root: Path, base: str) -> PlannedWrite:
    path = repo_root / base / "skills" / _SKILL_DIR / "SKILL.md"
    return PlannedWrite(path, _SKILL_MD)


def _render_hook_config(path: Path, matcher: str) -> str:
    if not path.exists():
        data: dict[str, Any] = {}
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AgentInitConfigError(f"{path}: expected valid JSON object") from exc
        if not isinstance(data, dict):
            raise AgentInitConfigError(f"{path}: expected a JSON object")
    if "hooks" not in data:
        hooks = {}
        data["hooks"] = hooks
    else:
        hooks = data["hooks"]
    if not isinstance(hooks, dict):
        raise AgentInitConfigError(f"{path}: hooks must be an object")
    if "PreToolUse" not in hooks:
        entries = []
    else:
        entries = hooks["PreToolUse"]
    if not isinstance(entries, list):
        raise AgentInitConfigError(f"{path}: hooks.PreToolUse must be an array")
    kept = [entry for entry in entries if not is_managed_hook_entry(entry, matcher)]
    hooks["PreToolUse"] = [*kept, managed_hook_entry(matcher)]
    return json.dumps(data, indent=2) + "\n"


def _managed_block() -> str:
    return f"{_MARK_BEGIN}\n{_AGENTS_HEADING}\n\n{_PROTOCOL_BODY.rstrip()}\n{_MARK_END}"


def _without_managed_block(text: str, path: Path) -> str:
    begin_count = text.count(_MARK_BEGIN)
    end_count = text.count(_MARK_END)
    if begin_count == 0 and end_count == 0:
        return text
    if begin_count != 1 or end_count != 1:
        raise AgentInitConfigError(f"{path}: expected zero or one PEBRA managed block")
    start = text.index(_MARK_BEGIN)
    end = text.index(_MARK_END)
    if end < start:
        raise AgentInitConfigError(f"{path}: PEBRA managed block markers are reversed")
    return text[:start] + text[end + len(_MARK_END):]


def _render_agents_md(repo_root: Path) -> PlannedWrite:
    path = repo_root / "AGENTS.md"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        raise AgentInitConfigError(f"{path}: expected a readable AGENTS.md") from exc
    base = _without_managed_block(existing, path).rstrip("\n")
    block = _managed_block()
    content = f"{base}\n\n{block}\n" if base else f"{block}\n"
    return PlannedWrite(path, content)
