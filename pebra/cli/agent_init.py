"""`pebra agent-init` — scaffold the PEBRA safe-edit protocol for a coding-agent host.

Phase 1 is agent-instruction / scaffolding ONLY. It writes the passive ``pebra-safe-edit`` skill and
host rules that tell an agent to CONSULT PEBRA before edits (assess -> read risk -> edit -> verify ->
record). It installs NO enforcement hook and imports no ``core``/``app``/``composition`` — enforcement
(the PreToolUse gate) is a separate, later slice. Templates are inline string constants so nothing
depends on package-data being shipped. Wording is deliberately "consult", not "blocks edits": Phase 1
makes no enforcement claim.

Targets:
- ``claude`` -> ``.claude/skills/pebra-safe-edit/SKILL.md``
- ``codex``  -> ``AGENTS.md`` (idempotent managed block; the reliable Codex surface) plus
  ``.agents/skills/pebra-safe-edit/SKILL.md`` (the documented repo-local Codex skills path).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SKILL_DIR = "pebra-safe-edit"
_MARK_BEGIN = "<!-- BEGIN pebra-safe-edit (managed by `pebra agent-init`) -->"
_MARK_END = "<!-- END pebra-safe-edit -->"

# The protocol body — shared by the SKILL.md (with frontmatter) and the AGENTS.md managed block.
# Must-consult wording only; no enforcement claim in Phase 1.
_PROTOCOL_BODY = """\
Before any significant edit, rename, or delete, you must consult PEBRA first. This is a pre-edit
obligation, not optional. Do not skip these steps:

1. **Assess (pre-edit).** Draft the intended change, then run
   `pebra assess <request.json> --json` with the target file(s) in `expected_files`. Read the returned
   decision, risk math, safe edit scope, and required checks before touching the code.
2. **Edit** within the safe scope PEBRA reports; keep to the smallest sufficient change.
3. **Verify.** After editing, run `pebra verify --assessment-id <id> --scope staged` and resolve any
   scope drift or build failures it reports.
4. **Record.** Run `pebra record-outcome --assessment-id <id> --status completed`.

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
        help="Scaffold the pebra-safe-edit skill / rules for a coding-agent host (Phase 1: instructions only).",
    )
    p.add_argument("--target", choices=("claude", "codex"), required=True)
    p.add_argument("--repo-root", default=".")
    p.set_defaults(func=run_agent_init)


def run_agent_init(args: Any) -> int:
    repo_root = Path(args.repo_root)
    if args.target == "claude":
        written = [_write_skill(repo_root, ".claude")]
    else:  # codex
        written = [_merge_agents_md(repo_root), _write_skill(repo_root, ".agents")]
    for path in written:
        print(f"wrote {path}")
    if args.target == "codex":
        print("note: AGENTS.md is the reliable Codex surface; .agents/skills is best-effort per Codex docs.")
    print("Phase 1 is instruction-only: no enforcement hook was installed.")
    return 0


def _write_skill(repo_root: Path, base: str) -> Path:
    path = repo_root / base / "skills" / _SKILL_DIR / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SKILL_MD, encoding="utf-8")
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
