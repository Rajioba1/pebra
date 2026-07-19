"""`pebra agent-init` — scaffold the PEBRA safe-edit protocol for a coding-agent host.

By default this writes the passive ``pebra-safe-edit`` skill and host rules that tell an agent to
CONSULT PEBRA before edits (assess -> read risk -> edit -> verify -> record). With ``--with-hook`` it
also writes a host hook config that calls ``pebra gate-hook`` before structured edits. Claude's
``.claude/settings.json`` hook is the supported enforcement surface; Codex's repo-local
``.codex/hooks.json`` is best-effort because Codex hook loading differs by host/plugin install.
Templates are inline string constants so nothing depends on package-data being shipped.

Targets:
- ``claude`` -> ``.claude/rules/pebra-safe-edit.md`` plus
  ``.claude/skills/pebra-safe-edit/SKILL.md``
- ``codex``  -> ``AGENTS.md`` (idempotent managed block; the reliable Codex surface) plus
  ``.agents/skills/pebra-safe-edit/SKILL.md`` (the documented repo-local Codex skills path).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pebra.adapters.path_safety import unsafe_managed_path as _unsafe_managed_path
from pebra.core.agent_hook_contract import (
    classify_hook_document,
    is_managed_hook_entry,
    managed_hook_entry,
)
from pebra.core.agent_hosts import AGENT_HOSTS
from pebra.core.gate_contract import GATE_SCHEMA_VERSION

_CLAUDE_HOOK_MATCHER = AGENT_HOSTS["claude"].hook_matcher
_CODEX_HOOK_MATCHER = AGENT_HOSTS["codex"].hook_matcher
_MARK_BEGIN = "<!-- BEGIN pebra-safe-edit (managed by `pebra agent-init`) -->"
_MARK_END = "<!-- END pebra-safe-edit -->"
PROTOCOL_VERSION = 1

# The protocol body — shared by the SKILL.md (with frontmatter) and the AGENTS.md managed block.
# Must-consult wording only; no enforcement claim in Phase 1.
_PROTOCOL_BODY = """\
Assess before every significant edit, rename, or delete: you must consult PEBRA first. This is a pre-edit
obligation, not optional. Do not skip these steps:

1. **Assess (pre-edit).** Draft the intended change, then run
   `pebra assess <request.json> --json` with the target file(s) in `expected_files` and the intended
   unified diff in `proposed_patch`. Read the returned decision, `scores.expected_loss`, safe edit
   scope, and required checks before touching the code. If the decision is `inspect_first`, inspect
   the reported dependents before resubmitting. If it is `test_first`, add or run the required tests
   before resubmitting.
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
   sanction, then will reassess the exact candidate. A PEBRA candidate hold or human review overrides
   an earlier advisory proceed for that exact candidate; it does not cancel the user's requested goal.
   PEBRA will apply it only after a
   `proceed` with `risk_mode=controlled_high_risk`. If the decision is `reject`, stop and ask for a different route.
   Never treat either decision as permission to edit.
4. **Edit.** When `next_action.type` is `apply_exact_candidate_then_verify`, run its returned command
   (`pebra apply-candidate --assessment-id <returned-id>`). Apply only the exact assessed candidate;
   do not manually retype or reconstruct the patch. Keep all later edits within the safe scope PEBRA reports.
5. **Verify.** After application, verify and record the outcome. Run
   `pebra verify --assessment-id <id> --scope staged` and resolve any scope drift or build failures it reports.
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

_CLAUDE_RULE_MD = """\
# PEBRA safe-edit non-negotiables

1. Assess before every significant edit, rename, or delete.
2. Never apply a mismatched or incomplete candidate; apply only the exact assessed candidate.
3. A PEBRA candidate hold or human review overrides an earlier advisory proceed for that exact
   candidate; it does not cancel the user's requested goal.
4. Never create, claim, or answer your own human sanction.
5. After application, verify and record the outcome.
"""

_AGENTS_HEADING = "## PEBRA safe-edit protocol"


@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    content: str
    newline: str | None = None


class AgentInitConfigError(ValueError):
    pass


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "agent-init",
        help="Scaffold the pebra-safe-edit skill/rules and optionally a host pre-edit gate hook.",
    )
    p.add_argument(
        "--target", choices=tuple(AGENT_HOSTS), required=True,
        help="Agent host whose PEBRA protocol files should be installed.",
    )
    p.add_argument(
        "--repo-root", default=".",
        help="Repository path (defaults to current directory).",
    )
    p.add_argument("--with-hook", action="store_true",
                   help="Also install a pre-edit gate hook config (Claude verified; Codex repo-local "
                        ".codex/hooks.json is best-effort and host-dependent). Default: instructions only.")
    p.add_argument(
        "--check", action="store_true",
        help="Inspect generated files, hook configuration, and effective enforcement without writing.",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable inspection output (requires --check).",
    )
    p.set_defaults(func=run_agent_init)


def run_agent_init(args: Any) -> int:
    try:
        repo_root = Path(args.repo_root).resolve()
    except (OSError, RuntimeError):
        print(f"agent-init: {args.repo_root}: cannot resolve repository root", file=sys.stderr)
        return 2
    with_hook = getattr(args, "with_hook", False)
    check = getattr(args, "check", False)
    as_json = getattr(args, "as_json", False)
    if as_json and not check:
        print("agent-init: --json requires --check", file=sys.stderr)
        return 2
    if check:
        return _run_check(repo_root, args.target, as_json=as_json)
    try:
        _reject_unsafe_managed_paths(repo_root, args.target, with_hook)
        planned = _plan_agent_init(repo_root, args.target, with_hook)
    except AgentInitConfigError as exc:
        print(f"agent-init: {exc}", file=sys.stderr)
        return 2
    for write in planned:
        write.path.parent.mkdir(parents=True, exist_ok=True)
        with write.path.open("w", encoding="utf-8", newline=write.newline) as destination:
            destination.write(write.content)
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


def _instruction_paths(repo_root: Path, target: str) -> tuple[Path, ...]:
    spec = AGENT_HOSTS[target]
    return tuple(
        repo_root / relative
        for relative in (*spec.instruction_paths, spec.skill_path)
    )


def _hook_path(repo_root: Path, target: str) -> Path:
    return repo_root / AGENT_HOSTS[target].hook_path


def _reject_unsafe_managed_paths(repo_root: Path, target: str, with_hook: bool) -> None:
    paths = list(_instruction_paths(repo_root, target))
    if with_hook:
        paths.append(_hook_path(repo_root, target))
    for path in paths:
        unsafe = _unsafe_managed_path(repo_root, path)
        if unsafe is not None:
            raise AgentInitConfigError(
                f"{unsafe}: managed path redirect or hardlink is not allowed"
            )


def _file_state(path: Path, expected: str, *, repo_root: Path) -> str:
    if _unsafe_managed_path(repo_root, path) is not None:
        return "modified"
    if not path.exists():
        return "absent"
    try:
        actual = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "modified"
    return "current" if actual == expected else "modified"


def _relative_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _inspection_files(repo_root: Path, target: str) -> list[dict[str, str]]:
    spec = AGENT_HOSTS[target]
    if target == "claude":
        expected = [
            PlannedWrite(repo_root / spec.instruction_paths[0], _CLAUDE_RULE_MD),
            _render_skill(repo_root, target),
        ]
    else:
        skill = _render_skill(repo_root, target)
        agents_path = repo_root / spec.instruction_paths[0]
        if _unsafe_managed_path(repo_root, agents_path) is not None:
            return [
                {"path": _relative_path(agents_path, repo_root), "state": "modified"},
                {
                    "path": _relative_path(skill.path, repo_root),
                    "state": _file_state(skill.path, skill.content, repo_root=repo_root),
                },
            ]
        try:
            agents = _render_agents_md(repo_root)
        except AgentInitConfigError:
            return [
                {"path": _relative_path(agents_path, repo_root), "state": "modified"},
                {
                    "path": _relative_path(skill.path, repo_root),
                    "state": _file_state(skill.path, skill.content, repo_root=repo_root),
                },
            ]
        expected = [agents, skill]
    return [
        {
            "path": _relative_path(write.path, repo_root),
            "state": _file_state(write.path, write.content, repo_root=repo_root),
        }
        for write in expected
    ]


def _inspect_hook_state(
    path: Path,
    matcher: str,
    *,
    host: Literal["claude", "codex"],
    repo_root: Path | None = None,
) -> str:
    if repo_root is not None and _unsafe_managed_path(repo_root, path) is not None:
        return "conflicting"
    if not path.exists():
        return "absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "malformed"
    if not isinstance(data, dict):
        return "malformed"
    return classify_hook_document(data, matcher, host=host)


def _check_payload(repo_root: Path, target: str) -> dict[str, Any]:
    # Lazy imports keep normal agent-init and parser construction dependency-light.
    from pebra.adapters import enforcement_capability  # noqa: PLC0415

    spec = AGENT_HOSTS[target]
    matcher = spec.hook_matcher
    hook_path = _hook_path(repo_root, target)
    enforcement = enforcement_capability.probe(repo_root, graph_available=None)
    return {
        "command": "agent-init",
        "target": target,
        "protocol_version": PROTOCOL_VERSION,
        "gate_schema_version": GATE_SCHEMA_VERSION,
        "files": _inspection_files(repo_root, target),
        "hook": {
            "path": _relative_path(hook_path, repo_root),
            "state": _inspect_hook_state(
                hook_path, matcher, host=target, repo_root=repo_root
            ),
        },
        "declared_support": spec.declared_support,
        "effective_enforcement": enforcement[target],
    }


def _run_check(repo_root: Path, target: str, *, as_json: bool) -> int:
    payload = _check_payload(repo_root, target)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"agent-init check - target: {payload['target']}")
    for item in payload["files"]:
        print(f"  {item['path']}: {item['state']}")
    print(f"  hook {payload['hook']['path']}: {payload['hook']['state']}")
    print(f"  declared support: {payload['declared_support']}")
    print(f"  effective mode: {payload['effective_enforcement']['mode']}")
    return 0


def _plan_agent_init(repo_root: Path, target: str, with_hook: bool) -> list[PlannedWrite]:
    spec = AGENT_HOSTS[target]
    if target == "claude":
        planned = [
            PlannedWrite(
                repo_root / spec.instruction_paths[0],
                _CLAUDE_RULE_MD,
                newline="",
            ),
            _render_skill(repo_root, target),
        ]
        if with_hook:
            path = repo_root / spec.hook_path
            planned.append(PlannedWrite(
                path, _render_hook_config(path, spec.hook_matcher, host="claude")
            ))
        return planned

    planned = [_render_agents_md(repo_root), _render_skill(repo_root, target)]
    if with_hook:
        path = repo_root / spec.hook_path
        planned.append(PlannedWrite(
            path, _render_hook_config(path, spec.hook_matcher, host="codex")
        ))
    return planned


def _render_skill(repo_root: Path, target: str) -> PlannedWrite:
    path = repo_root / AGENT_HOSTS[target].skill_path
    return PlannedWrite(path, _SKILL_MD, newline="")


def _render_hook_config(
    path: Path, matcher: str, *, host: Literal["claude", "codex"]
) -> str:
    if not path.exists():
        data: dict[str, Any] = {}
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AgentInitConfigError(f"{path}: expected valid JSON object") from exc
        if not isinstance(data, dict):
            raise AgentInitConfigError(f"{path}: expected a JSON object")
    state = classify_hook_document(data, matcher, host=host)
    if state in {"conflicting", "malformed"}:
        raise AgentInitConfigError(
            f"{path}: hook configuration is {state}; inspect with --check --json "
            "and resolve it before installing the hook"
        )
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


def _managed_block(newline: str = "\n") -> str:
    block = f"{_MARK_BEGIN}\n{_AGENTS_HEADING}\n\n{_PROTOCOL_BODY.rstrip()}\n{_MARK_END}"
    return block.replace("\n", newline)


def _managed_block_span(text: str, path: Path) -> tuple[int, int] | None:
    begin_count = text.count(_MARK_BEGIN)
    end_count = text.count(_MARK_END)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise AgentInitConfigError(f"{path}: expected zero or one PEBRA managed block")
    start = text.index(_MARK_BEGIN)
    end = text.index(_MARK_END)
    if end < start:
        raise AgentInitConfigError(f"{path}: PEBRA managed block markers are reversed")
    return start, end + len(_MARK_END)


def _render_agents_md(repo_root: Path) -> PlannedWrite:
    path = repo_root / "AGENTS.md"
    try:
        existing = path.read_bytes().decode("utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentInitConfigError(f"{path}: expected a readable UTF-8 AGENTS.md") from exc
    newline = "\r\n" if "\r\n" in existing else "\n"
    block = _managed_block(newline)
    span = _managed_block_span(existing, path)
    if span is not None:
        start, end = span
        content = existing[:start] + block + existing[end:]
    elif not existing:
        content = block + newline
    else:
        if existing.endswith(newline * 2):
            separator = ""
        elif existing.endswith(newline):
            separator = newline
        else:
            separator = newline * 2
        content = existing + separator + block + newline
    return PlannedWrite(path, content, newline="")
