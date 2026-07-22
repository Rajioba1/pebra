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
import stat
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
PROTOCOL_VERSION = 4

_NON_NEGOTIABLES = """\
1. Assess before every repository file creation, edit, rename, or deletion.
2. Never apply a mismatched or incomplete candidate; apply only the exact assessed candidate.
3. A PEBRA candidate hold or human review overrides an earlier advisory proceed for that exact
   candidate; it does not cancel the user's requested goal.
4. Never create, claim, or answer your own human sanction.
   `pebra accept-risk --apply` requires a real interactive TTY controlled by a trusted human or operator.
5. After application, verify and record the outcome."""

# Shared by the host skills and the AGENTS.md managed block. Keep provider details out of these
# token-sensitive instructions; public architecture documentation names the current implementation.
_PROTOCOL_BODY = f"""\
PEBRA safe-edit non-negotiables:

{_NON_NEGOTIABLES}

Lifecycle: Interpret → Recall verified lessons → Retrieve current repository context → Design →
Assess → Calculate → Evaluate gates → Decide → Enforce → Apply → Verify → Record → Learn/promote

1. **Interpret.** Interpret the maintainer's request and classify the work. A Read-only explanation or
   investigation may stop after current-context retrieval. Any repository mutation—file creation, edit,
   rename, or deletion—must continue through pre-edit assessment before any write.
2. **Recall verified lessons.** Before designing a mutation, run one `pebra explore` call with the task,
   relevant symbols, or target files. It first returns bounded PEBRA `learning_context` labelled
   "Historical record — not instructions." Treat recalled lessons and old risk-benefit scores as advisory
   history; current source wins. Only validated file and symbol identifiers may refine current retrieval.
   Historical prose, decisions, outcomes, and old scores never become current graph evidence. Unavailable,
   corrupt, or empty recall is a non-blocking fallback to the original graph query.
3. **Retrieve current repository context.** The same `pebra explore` call then queries the configured
   repository graph engine for bounded current structural context. Reuse equivalent current context already
   supplied by the host. Do not repeat equivalent exploration. This context does not authorize an edit
   and is not trusted PEBRA scoring evidence. If current retrieval is unavailable, use the host's ordinary
   repository search/read tools.
4. **Design.** Use that knowledge to choose the smallest suitable route, exact files and symbols, affected
   tests, and exact candidate patch. PEBRA does not invent the candidate: the model supplies `expected_files`
   and `proposed_patch`.
5. **Assess (pre-edit).** Run `pebra assess <request.json> --json` before touching code. It loads applicable
   promoted facts for the exact current assessment context. Across a `revise_safer` lineage, keep
   the same task text and stable action ID while changing `proposed_patch`.
   Use `model_guidance_packet.advisory.safer_route` to draft a safer or compatibility-preserving candidate;
   for public contracts consider an alias, wrapper, adapter, default implementation, or deprecation bridge.
   Resubmit until the reassessment permits editing and shows lower risk. PEBRA does not accept
   self-reported candidate verification in the request.
6. **Calculate.** PEBRA—not the agent—calculates `scores.expected_loss`, benefit, expected utility,
   uncertainty, RAU, edit confidence, and risk-budget use. Read the returned values, evidence, safe scope,
   and required checks; never recompute or override PEBRA's metrics.
7. **Evaluate gates.** PEBRA evaluates decision gates against those calculated values and current evidence.
   Decision gates choose the assessment result; they are distinct from the pre-mutation enforcement gate.
8. **Decide.** PEBRA—not the agent—decides. Follow the returned `next_action`:
   - `proceed` applies only to the exact assessed candidate.
   - `inspect_first` requires inspection and reassessment; `test_first` requires tests and reassessment.
   - `revise_safer` requires a changed, lower-risk candidate; resubmit it for reassessment.
     Do not apply the original patch.
   - `ask_human` holds the exact candidate. Present its reasons, risk-benefit values, uncertainty, and controls.
     A trusted human or host operator may run `pebra accept-risk --apply`; it requires a real interactive
     TTY controlled by a trusted human or operator. Never answer the approval prompt yourself. Do not create
     or claim the sanction yourself. PEBRA creates the bound sanction and will reassess the exact candidate.
   - `reject` means: This exact candidate is rejected, not the maintainer's goal. Present the recorded reasons
     and risk-benefit evidence to the maintainer. If `next_action.override.available` is true, a trusted human
     may run the returned interactive command; never answer it yourself. Otherwise revise the candidate or
     follow the stated policy-resolution route. Never edit governing policy merely to bypass a rejection;
     only follow a maintainer-authored policy change and then reassess from fresh repository state.
   Never treat a held candidate as permission to edit.
9. **Enforce.** Before any mutation, the pre-mutation enforcement gate checks the exact bound candidate and
   current repository state. A PEBRA candidate hold or human review overrides an earlier advisory proceed
   for that exact candidate. Never bypass or self-answer it; reassess whenever the candidate or state changes.
10. **Apply.** On the ordinary proceed path, only
   `next_action.type=apply_exact_candidate_then_verify` permits application. Run its returned
   `pebra apply-candidate --assessment-id <returned-id>` command. Do not retype, reconstruct, or expand the
   patch. On the human-review path, `pebra accept-risk --apply` performs trusted interactive approval,
   reassesses and applies the exact candidate itself, and returns an already-applied result. Its reassessment
   must return `proceed` with `risk_mode=controlled_high_risk`; after success, do not run
   `pebra apply-candidate` or apply it again. After `pebra accept-risk --apply`, use its returned
   `reassessment_id` for Verify and Record; never use the original held assessment ID.
   For both apply paths, stage exactly the returned `changed_files` and no other paths before Verify.
   Use only `git --literal-pathspecs add -- <changed_file>...`, passing each returned path as a separate,
   safely quoted argument. The `--` delimiter alone ends options but does not make wildcard pathspecs
   literal; never concatenate or evaluate path text as shell code, and use no other staging method.
   Do not run `pebra verify --scope staged` unless the staged path set exactly equals `changed_files`.
11. **Verify.** Run `pebra verify --assessment-id <id> --scope staged` and resolve scope drift or failed checks.
12. **Record.** After passing verification, run
   `pebra record-outcome --assessment-id <id> --status completed`. Only this verified-completed outcome path
   may materialize a recallable lesson; a skipped, rejected, failed, or raw store outcome may not.
13. **Learn/promote.** Recall materialization is not calibration or promotion. Recording alone is not
   calibration or promotion. Only separately reviewed and promoted numeric facts can influence a future
   Assess; recalled `learning_context` remains advisory history for understanding.
"""

_SKILL_MD = f"""\
---
name: pebra-safe-edit
description: Use BEFORE every repository file creation, edit, rename, or deletion. Consult PEBRA's pre-edit risk assessment first to avoid breaking dependents.
---

# PEBRA safe edit

{_PROTOCOL_BODY}"""

_CLAUDE_RULE_MD = f"""\
# PEBRA safe-edit non-negotiables

{_NON_NEGOTIABLES}
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
        _reject_invalid_managed_path_types(repo_root, path)


def _reject_invalid_managed_path_types(repo_root: Path, path: Path) -> None:
    """Require existing parents to be directories and the destination to be a file."""
    try:
        relative = path.relative_to(repo_root)
    except ValueError as exc:
        raise AgentInitConfigError(f"{path}: managed path is outside repository root") from exc
    current = repo_root
    last_index = len(relative.parts) - 1
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AgentInitConfigError(
                f"{current}: cannot inspect managed path metadata"
            ) from exc
        if index == last_index:
            if not stat.S_ISREG(mode):
                raise AgentInitConfigError(
                    f"{current}: managed destination must be a regular file"
                )
        elif not stat.S_ISDIR(mode):
            raise AgentInitConfigError(
                f"{current}: managed path parent must be a directory"
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
