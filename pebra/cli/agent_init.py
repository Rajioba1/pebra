"""`pebra agent-init` — scaffold the PEBRA safe-edit protocol for a coding-agent host.

By default this writes the passive ``pebra-safe-edit`` skill and host rules that tell an agent to
CONSULT PEBRA before edits (assess -> read risk -> edit -> verify -> record). With ``--with-hook`` it
also writes a host hook config that calls ``pebra gate-hook`` before structured edits. Claude's
``.claude/settings.json`` hook is the verified enforcement surface; Codex's repo-local
``.codex/hooks.json`` is best-effort because Codex hook loading differs by host/plugin install.
Templates are inline string constants so nothing depends on package-data being shipped.

Targets:
- ``claude`` -> ``.claude/skills/pebra-safe-edit/SKILL.md``
- ``codex``  -> ``AGENTS.md`` (idempotent managed block; the reliable Codex surface) plus
  ``.agents/skills/pebra-safe-edit/SKILL.md`` (the documented repo-local Codex skills path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SKILL_DIR = "pebra-safe-edit"
_CLAUDE_HOOK_MATCHER = "Edit|Write|MultiEdit"
_CODEX_HOOK_MATCHER = "apply_patch"
_HOOK_COMMAND = "pebra gate-hook"
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
   `model_guidance_packet.advisory.safer_route` to draft a narrower candidate, then resubmit by running
   `pebra assess` again with that new `proposed_patch`. If `safer_route.candidate_verification` is
   present, check the revised candidate before editing the real worktree (for example in a scratch
   worktree/clone), then include the result in `evidence.candidate_verification` on the reassessment.
   Edit only after the reassessment permits editing and shows lower risk (for example, lower
   `scores.expected_loss`, a passed candidate verification, or a less severe decision).
3. **Escalate when asked.** If the decision is `ask_human` or `reject`, stop and ask the user for
   approval or a different route; do not treat it as permission to edit.
4. **Edit** within the safe scope PEBRA reports; keep to the smallest sufficient change.
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


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "agent-init",
        help="Scaffold the pebra-safe-edit skill/rules and optionally a host pre-edit gate hook.",
    )
    p.add_argument("--target", choices=("claude", "codex"), required=True)
    p.add_argument("--repo-root", default=".")
    p.add_argument("--with-hook", action="store_true",
                   help="Also install a pre-edit gate hook config (Claude verified; Codex repo-local "
                        ".codex/hooks.json is best-effort and host-dependent). Default: instructions only.")
    p.set_defaults(func=run_agent_init)


def run_agent_init(args: Any) -> int:
    repo_root = Path(args.repo_root)
    with_hook = getattr(args, "with_hook", False)
    if args.target == "claude":
        written = [_write_skill(repo_root, ".claude")]
        if with_hook:
            written.append(_install_hook(repo_root, Path(".claude") / "settings.json", _CLAUDE_HOOK_MATCHER))
    else:  # codex
        written = [_merge_agents_md(repo_root), _write_skill(repo_root, ".agents")]
        if with_hook:
            written.append(_install_hook(repo_root, Path(".codex") / "hooks.json", _CODEX_HOOK_MATCHER))
    for path in written:
        print(f"wrote {path}")
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


def _write_skill(repo_root: Path, base: str) -> Path:
    path = repo_root / base / "skills" / _SKILL_DIR / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SKILL_MD, encoding="utf-8")
    return path


def _is_pebra_gate_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    return any(isinstance(h, dict) and "gate-hook" in str(h.get("command", ""))
               for h in entry.get("hooks", []))


def _install_hook(repo_root: Path, rel_path: Path, matcher: str) -> Path:
    """Merge the PreToolUse gate hook into a host hooks file, preserving unrelated settings and other
    hooks, and idempotently (re-run never duplicates the entry). Malformed JSON is replaced."""
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    data: Any = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list):
        pre = []
    pre = [e for e in pre if not _is_pebra_gate_hook(e)]  # drop any prior pebra entry -> idempotent
    pre.append({"matcher": matcher,
                "hooks": [{"type": "command", "command": _HOOK_COMMAND}]})
    hooks["PreToolUse"] = pre
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _managed_block() -> str:
    return f"{_MARK_BEGIN}\n{_AGENTS_HEADING}\n\n{_PROTOCOL_BODY.rstrip()}\n{_MARK_END}"


def _without_managed_block(text: str) -> str:
    start = text.find(_MARK_BEGIN)
    end = text.find(_MARK_END)
    if start == -1 or end == -1:
        return text
    return text[:start] + text[end + len(_MARK_END):]


def _merge_agents_md(repo_root: Path) -> Path:
    path = repo_root / "AGENTS.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    base = _without_managed_block(existing).rstrip("\n")
    block = _managed_block()
    content = f"{base}\n\n{block}\n" if base else f"{block}\n"
    path.write_text(content, encoding="utf-8")
    return path
