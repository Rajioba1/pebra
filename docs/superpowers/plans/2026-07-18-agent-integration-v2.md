# PEBRA 0.1.1 And Agent Integration V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely ship PEBRA `0.1.1` with complete CLI/TUI discoverability and the `agent-init` release blockers, then build Agent Integration V2 and align the existing agent A/B experiment with the completed production behavior.

**Architecture:** First close the two destructive `agent-init` edge cases, expose the already-wired CLI/TUI help and provenance behavior, and release only that bounded work as `0.1.1` through the existing build-once workflow. After the release checkpoint, preserve the existing gate and generated protocol while adding dependency-free contracts for candidate binding, gate permissions/tiers, and host facts. The final milestone makes the agent A/B subprocess harness an explicit schema-1 consumer without changing treatment, blinding, or model-facing results.

**Tech Stack:** Python 3.11+, `argparse`, frozen dataclasses, `enum.StrEnum`, JSON, pytest, Ruff, import-linter, nox, GitHub Actions.

## Global Constraints

- Work on `main`; do not create a feature branch unless the maintainer changes this instruction.
- Use test-first development for every behavior change.
- `0.1.1` contains only the validation-safe hook installation, exact hook ownership, CLI/TUI help discoverability, documentation, and version/release updates defined by `docs/superpowers/specs/2026-07-18-cli-help-and-0.1.1-release-design.md`.
- Typed gate contracts, Claude always-loaded rules, inspection, the host registry, and experiment alignment remain post-`0.1.1` work even though this is one implementation plan.
- Preserve `allow/fail_open` for graph, Git, store, parse, and unexpected hook-runtime failures.
- Host wrappers branch on `GatePermission`; `GateTier` is diagnostic and cannot independently authorize an edit.
- Preserve the A/B treatment: call the real gate subprocess with `consult_only=True`, attribute an assessment only after a successful write, and expose exactly `{ok, blocked, reason}` to the model in every arm.
- Keep `positive_control` as an experiment-local synthetic label; never add it to the production `GateTier` enum or present it as a versioned production gate response.
- Before each review stop, run the milestone's focused subprocess E2E acceptance tests. Defer the complete deterministic A/B suite and `nox -s e2e-fast` to the final experiment milestone because they are the expensive aggregate proof.
- Never overwrite malformed user configuration or delete a lookalike user hook.
- Treat `pebra gate-hook` as an installed compatibility invariant. Do not change it without an explicit
  legacy-signature allowlist and one-legacy-entry-to-one-current-entry migration tests for both hosts.
- Never modify user content outside PEBRA's existing managed block.
- Materialize complete skill/rule content; no symlinks, pointer files, external imports, or self-updater.
- Do not add a third agent runtime in this plan.
- `DEVELOPMENT.md` is intentionally ignored/local: keep its examples current in this workspace, but never
  force-add it to a release commit.
- Do not push, tag, publish, or continue past a `STOP FOR REVIEW` without maintainer approval.

## Source Designs And Status

- Release/CLI source: `docs/superpowers/specs/2026-07-18-cli-help-and-0.1.1-release-design.md`.
- Agent-integration source: `docs/superpowers/specs/2026-07-18-agent-integration-v2-design.md`.
- `docs/superpowers/plans/2026-07-18-refresh-interaction-state.md` is completed historical work
  implemented by `0357a22`; it is not an active second plan and none of its finished changes are repeated
  here.

## Design-To-Task Map

| Approved design requirement | Implementation task |
|---|---|
| Validation-first writes and exact PEBRA hook ownership | Task 1 |
| Discoverable lazy `--version` / `-V` | Task 2 |
| TUI `? pebra --help` and README command surface | Task 3 |
| `0.1.1` metadata, docs, archive, and installed-wheel proof | Task 4 |
| Protected build-once TestPyPI → PyPI release | Task 5 |
| Single candidate-binding algorithm | Task 6 |
| Typed/versioned gate contract and documentation | Task 7 |
| Real subprocess gate-envelope acceptance | Task 8 |
| Always-loaded Claude non-negotiables | Task 9 |
| Non-mutating `agent-init --check --json` | Task 10 |
| Two-host registry and conformance matrix | Task 11 |
| Final A/B schema alignment and aggregate E2E | Task 12 |

The maintainer's sequencing decision supersedes the release design's generic instruction to run all fast
E2E before `0.1.1`: each pre-release milestone runs focused subprocess E2E plus the full normal test/lint
and hosted CI gates, while the expensive complete deterministic A/B/`e2e-fast` aggregate runs only after
all production integration milestones, in Task 12.

---

## Milestone 0 — 0.1.1 Release Safety

### Task 1: Validation-first agent initialization and exact hook ownership

**Files:**
- Create: `pebra/core/agent_hook_contract.py`
- Modify: `pebra/cli/agent_init.py:22-186`
- Modify: `pebra/adapters/enforcement_capability.py:1-41`
- Modify: `tests/unit/test_agent_init.py:14-257`
- Modify: `tests/unit/test_enforcement_capability.py`
- Create: `e2e/features/agent/test_agent_init_safety.py`

**Interfaces:**
- Produces: `HOOK_COMMAND: Final[str]`, `managed_hook_entry(matcher: str) -> dict[str, object]`, and `is_managed_hook_entry(value: object, matcher: str) -> bool`.
- Produces: an internal validation-first `PlannedWrite(path: Path, content: str)` list consumed by `run_agent_init`.
- Preserves: `run_agent_init(args) -> int` and the current CLI syntax.

- [ ] **Step 1: Write failing ownership regressions**

Add tests proving an exact PEBRA entry is recognized but lookalikes are not:

```python
def test_claude_with_hook_preserves_lookalike_gate_hook_command(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    lookalike = {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
    }
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [lookalike]}}), encoding="utf-8")

    assert _run_with_hook("claude", tmp_path) == 0

    entries = _pre_tool_use(settings)
    assert lookalike in entries
    assert sum(entry == agent_init.managed_hook_entry("Edit|Write|MultiEdit") for entry in entries) == 1
```

Add the equivalent capability test:

```python
def test_hook_probe_rejects_lookalike_command(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [{"type": "command", "command": "echo run-my-gate-hook-check"}],
        }]},
    }), encoding="utf-8")

    assert enforcement_capability._hook_installed(settings, "Edit|Write|MultiEdit") is False
```

Lock the installed command identity so a future rename cannot silently strand old entries:

```python
from pebra.core import agent_hook_contract


def test_hook_command_is_the_installed_v2_compatibility_contract():
    assert agent_hook_contract.HOOK_COMMAND == "pebra gate-hook"
```

This is intentionally a hardcoded compatibility regression. A future command change must update this
test only alongside an explicit allowlist of the prior complete owned signatures and parameterized tests
proving each legacy entry is replaced by exactly one current entry for Claude and Codex. Do not introduce
an ownership metadata key without first verifying both host schemas accept it.

- [ ] **Step 2: Write failing malformed/no-partial-write regressions**

Parameterize malformed JSON and invalid structural shapes:

```python
@pytest.mark.parametrize(
    "raw",
    (
        "{broken",
        "null",
        "[]",
        '{"hooks": []}',
        '{"hooks": null}',
        '{"hooks": {"PreToolUse": {}}}',
        '{"hooks": {"PreToolUse": null}}',
    ),
)
def test_agent_init_with_hook_rejects_invalid_config_without_any_write(tmp_path, raw, capsys):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(raw, encoding="utf-8")

    assert _run_with_hook("claude", tmp_path) == 2

    assert settings.read_text(encoding="utf-8") == raw
    assert not (tmp_path / _SKILL_REL).exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert str(settings) in capsys.readouterr().err
```

Add the Codex variant and a valid-settings preservation test. Explicit JSON `null` is invalid when a key
is present; only an absent key receives a default container. Validation failure must not create either
instruction files or hook files.

Add managed-block corruption regressions for Codex:

```python
@pytest.mark.parametrize(
    "raw",
    (
        f"user text\n{agent_init._MARK_BEGIN}\nunterminated\n",
        f"{agent_init._MARK_END}\nuser text\n{agent_init._MARK_BEGIN}\n",
        f"{agent_init._MARK_BEGIN}\na\n{agent_init._MARK_END}\n"
        f"{agent_init._MARK_BEGIN}\nb\n{agent_init._MARK_END}\n",
    ),
)
def test_codex_rejects_corrupt_managed_markers_without_any_write(tmp_path, raw):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(raw, encoding="utf-8")

    assert _run_with_hook("codex", tmp_path) == 2

    assert agents.read_text(encoding="utf-8") == raw
    assert not (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").exists()
    assert not (tmp_path / ".codex/hooks.json").exists()
```

Also cover an unmatched end marker and a nested/duplicate begin marker. Exactly zero marker pairs or one
ordered pair is valid; unmatched, reversed, nested, or duplicate markers are validation errors.

Add a process-boundary regression in `e2e/features/agent/test_agent_init_safety.py` so the milestone is
not accepted solely from in-process unit tests:

```python
from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("target", "config_rel", "skill_rel"),
    (
        ("claude", ".claude/settings.json", ".claude/skills/pebra-safe-edit/SKILL.md"),
        ("codex", ".codex/hooks.json", ".agents/skills/pebra-safe-edit/SKILL.md"),
    ),
)
@pytest.mark.parametrize(
    "raw",
    (
        "{broken",
        '{"hooks": null}',
        '{"hooks": {"PreToolUse": null}}',
    ),
)
def test_agent_init_malformed_hook_is_failure_atomic(
    tmp_path, target, config_rel, skill_rel, raw,
):
    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    config.write_text(raw, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", target,
            "--repo-root", str(tmp_path), "--with-hook",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )

    assert result.returncode == 2
    assert config.read_text(encoding="utf-8") == raw
    assert not (tmp_path / skill_rel).exists()
    assert not (tmp_path / "AGENTS.md").exists()
```

Add a second parameterized process test starting from a valid config containing a lookalike command. It
must exit `0`, preserve that complete entry, and add exactly one structurally exact `pebra gate-hook`
entry with the target's matcher.

Add a Codex process test parameterized over the same corrupt `AGENTS.md` marker shapes. It must return
`2`, leave `AGENTS.md` byte-identical, and create neither the skill nor `.codex/hooks.json`.

- [ ] **Step 3: Run focused tests and confirm both bugs reproduce**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py e2e/features/agent/test_agent_init_safety.py -q
```

Expected: the lookalike is deleted and malformed input is overwritten or partially installs files.

- [ ] **Step 4: Add the dependency-free ownership contract**

Create `pebra/core/agent_hook_contract.py`:

```python
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
```

Import these functions from both `agent_init.py` and `enforcement_capability.py`. Capability observation
must use `any(is_managed_hook_entry(entry, expected_matcher) for entry in entries)`.

- [ ] **Step 5: Render a complete validated write plan before writing**

In `agent_init.py`, separate rendering from filesystem mutation:

```python
@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    content: str


class AgentInitConfigError(ValueError):
    pass


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
```

Validate the user-owned `AGENTS.md` envelope before splicing:

```python
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
```

Pass the destination path from the pure `AGENTS.md` renderer. Add pure renderers for the skill and
managed `AGENTS.md` content. `_plan_agent_init` must read and render
every destination, including the hook, before returning any `PlannedWrite`. `run_agent_init` catches
`AgentInitConfigError`, prints `agent-init: <message>` to stderr, returns `2`, and performs no writes.
Only after planning succeeds may it create parent directories and write every planned content string.

- [ ] **Step 6: Run focused safety tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py -q
```

Expected: all tests pass, including byte-identical preservation and lookalike ownership cases.

- [ ] **Step 7: Run Milestone 0 verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/features/agent/test_agent_init_safety.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
git diff --check
```

Expected: the focused subprocess acceptance tests prove failure atomicity and exact ownership, Ruff
passes, all import contracts are kept, and the diff check is clean.

- [ ] **Step 8: Commit Milestone 0**

```powershell
git add pebra/core/agent_hook_contract.py pebra/cli/agent_init.py pebra/adapters/enforcement_capability.py tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py e2e/features/agent/test_agent_init_safety.py
git commit -m "fix: make agent hook installation validation-safe"
```

### STOP FOR REVIEW 0

Report the commit, changed files, focused/full verification, and direct evidence that malformed config and
lookalike hooks are preserved. Do not proceed or release `0.1.1` without maintainer approval.

---

## Milestone 1 — CLI/TUI Discoverability And The 0.1.1 Candidate

### Task 2: Register lazy root version flags

**Files:**
- Modify: `pebra/cli/main.py:1-130`
- Create: `tests/unit/test_cli_version.py`
- Modify: `tests/unit/test_cli_help.py`
- Modify: `tests/unit/test_cli_tui.py:119-131`
- Create: `e2e/smoke/test_cli_discovery.py`

**Interfaces:**
- Produces: `_LazyVersionAction(argparse.Action)` registered as `--version` and `-V` on the root parser.
- Preserves: `provenance_line()` as the only renderer and invokes it only when a version flag is selected.
- Removes: the pre-parser `raw_args[0]` special case in `main()`.

- [ ] **Step 1: Write failing unit tests for discoverability and laziness**

Create `tests/unit/test_cli_version.py`:

```python
from __future__ import annotations

import pytest

from pebra import provenance
from pebra.cli import main


def test_root_help_lists_both_version_flags():
    help_text = main.build_parser().format_help()
    assert "--version" in help_text
    assert "-V" in help_text


@pytest.mark.parametrize("flag", ("--version", "-V"))
def test_version_flag_renders_provenance_lazily(flag, monkeypatch, capsys):
    calls = 0

    def render() -> str:
        nonlocal calls
        calls += 1
        return "PEBRA 0.1.1 (editable abc1234)"

    monkeypatch.setattr(provenance, "provenance_line", render)
    with pytest.raises(SystemExit) as stopped:
        main.main([flag])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == "PEBRA 0.1.1 (editable abc1234)\n"
    assert calls == 1


def test_parser_build_and_help_do_not_compute_provenance(monkeypatch):
    monkeypatch.setattr(
        provenance, "provenance_line", lambda: pytest.fail("provenance must remain lazy"),
    )
    parser = main.build_parser()
    assert "--version" in parser.format_help()
```

Extend `tests/unit/test_cli_help.py::test_help_lists_every_live_command_with_discovery_syntax` to assert
the rendered root help also contains `--version` and `-V`.

Update the existing duplicate version test in `tests/unit/test_cli_tui.py` to expect `SystemExit(0)`
instead of a returned `0`; retain its real provenance assertions. This is the intentional argparse
behavior change and prevents the old expectation from failing the full suite.

- [ ] **Step 2: Run the focused tests and verify the parser does not expose the flags**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_version.py tests/unit/test_cli_help.py tests/unit/test_cli_tui.py -q
```

Expected: the help assertions fail because version is still handled before parser construction.

- [ ] **Step 3: Implement the lazy argparse action**

Add this action above `build_parser()`:

```python
class _LazyVersionAction(argparse.Action):
    """Render provenance only when argparse selects a version flag."""

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str = argparse.SUPPRESS,
        default: object = argparse.SUPPRESS,
        help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=0,
            default=default,
            help=help,
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        from pebra.provenance import provenance_line

        parser.exit(message=f"{provenance_line()}\n")
```

Register it immediately after constructing the root parser:

```python
parser.add_argument(
    "--version", "-V", action=_LazyVersionAction,
    help="Show version, install mode, and source revision, then exit.",
)
```

Delete `raw_args` and the early-return block from `main()`. Keep `_configure_output_streams()` before
parser construction so legacy-console output remains fail-soft.

- [ ] **Step 4: Add the real-process discovery test**

Create `e2e/smoke/test_cli_discovery.py`:

```python
from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pebra", *args],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_root_help_and_version_are_discoverable_over_real_cli():
    help_result = _run("--help")
    assert help_result.returncode == 0
    assert "--version" in help_result.stdout
    assert "-V" in help_result.stdout

    for flag in ("--version", "-V"):
        version_result = _run(flag)
        assert version_result.returncode == 0
        assert version_result.stdout.startswith("PEBRA ")
        assert ("editable" in version_result.stdout) or ("installed" in version_result.stdout)
```

- [ ] **Step 5: Verify and commit lazy version help**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_version.py tests/unit/test_cli_help.py tests/unit/test_cli_tui.py e2e/smoke/test_cli_discovery.py e2e/test_boundary_discipline.py -q
git diff --check
git add pebra/cli/main.py tests/unit/test_cli_version.py tests/unit/test_cli_help.py tests/unit/test_cli_tui.py e2e/smoke/test_cli_discovery.py
git commit -m "feat: expose lazy version flags in cli help"
```

### Task 3: Add the TUI help footer and complete command documentation

**Files:**
- Modify: `pebra/tui/app.py:30-65`
- Modify: `tests/integration/test_tui_app.py:20-65`
- Modify: `tests/snapshots/__snapshots__/test_tui_snapshots/*.svg`
- Modify: `README.md:25-115`
- Modify: `tests/unit/test_project_metadata.py`

**Interfaces:**
- Produces: visible `? pebra --help` binding that invokes Textual's inherited
  `action_show_help_panel()`.
- Preserves: `q` convenience quit and inherited priority `ctrl+q`; no subprocess or custom help screen.
- Documents: installed/editable TUI launch, version, root help, command help, and complete help.

- [ ] **Step 1: Write the failing TUI binding test**

Add to `tests/integration/test_tui_app.py`:

```python
def test_question_mark_footer_binding_opens_textual_help_panel() -> None:
    from textual.widgets import HelpPanel

    async def scenario() -> None:
        app = ObservatoryApp(_ctx())
        async with app.run_test() as pilot:
            binding = app.active_bindings["question_mark"].binding
            assert binding.action == "show_help_panel"
            assert binding.description == "pebra --help"
            assert len(app.query(HelpPanel)) == 0
            await pilot.press("?")
            await pilot.pause()
            assert app.query_one(HelpPanel) is not None

    asyncio.run(scenario())
```

Retain the existing tests proving `q` and inherited priority `ctrl+q` still quit.

- [ ] **Step 2: Run the focused TUI test and verify the binding is absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_tui_app.py::test_question_mark_footer_binding_opens_textual_help_panel -q
```

Expected: `app.active_bindings["question_mark"]` raises because Textual normalizes `?` to
`question_mark` and the footer binding is not registered.

- [ ] **Step 3: Reuse Textual's built-in help action**

Change only the app binding declaration:

```python
BINDINGS = [
    ("q", "quit", "Quit"),
    ("?", "show_help_panel", "pebra --help"),
]
```

Do not change `get_system_commands`; its existing Help command already calls the same inherited action.

- [ ] **Step 4: Lock the README command surface**

Add a concise terminal-dashboard/help section to `README.md` containing these exact commands:

```powershell
pebra tui --repo-root .
.\.venv\Scripts\python.exe -m pebra tui --repo-root .
pebra --version
pebra --help
pebra help tui
pebra help --all
```

Add to `tests/unit/test_project_metadata.py`:

```python
def test_readme_documents_cli_and_tui_discovery_commands() -> None:
    body = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        "pebra tui --repo-root .",
        r".\.venv\Scripts\python.exe -m pebra tui --repo-root .",
        "pebra --version",
        "pebra --help",
        "pebra help tui",
        "pebra help --all",
    ):
        assert command in body
```

- [ ] **Step 5: Run focused E2E, verify, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_tui_app.py tests/unit/test_project_metadata.py e2e/smoke/test_cli_discovery.py -q
.\.venv\Scripts\python.exe -m pytest tests/snapshots/test_tui_snapshots.py --snapshot-update -q
.\.venv\Scripts\python.exe -m pytest tests/snapshots/test_tui_snapshots.py -q
git diff --check
git add pebra/tui/app.py tests/integration/test_tui_app.py README.md tests/unit/test_project_metadata.py tests/snapshots/__snapshots__/test_tui_snapshots/*.svg
git commit -m "feat: surface cli help in the observatory"
```

Visually inspect all six regenerated SVGs before staging. The only intended snapshot change is the visible
`? pebra --help` footer entry; ledger, banner, detail, colors, and data must remain unchanged.

### Task 4: Build the immutable 0.1.1 candidate locally

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `DEVELOPMENT.md:85-100`
- Modify: `RELEASING.md:24-80`
- Modify: `scripts/verify_distribution.py:129-188`
- Modify: `tests/unit/test_project_metadata.py`
- Modify: `tests/unit/test_distribution_verifier.py`

**Interfaces:**
- Changes: the single package version source from `0.1.0` to `0.1.1`.
- Preserves: self-contained distribution-verifier fixtures that intentionally model `0.1.0` archives.
- Produces: local wheel/sdist evidence matching the future annotated tag `v0.1.1`.

- [ ] **Step 1: Write the failing live-metadata version test**

Add to `tests/unit/test_project_metadata.py`:

```python
def test_project_version_is_0_1_1_release_candidate() -> None:
    _, project = _project_metadata()
    assert project["version"] == "0.1.1"
```

Add a source assertion to `tests/unit/test_distribution_verifier.py` proving installed verification
compares `--version` output with `importlib.metadata.version("pebra")`; do not rewrite its `0.1.0` mock
archive names.

- [ ] **Step 2: Run the focused test and verify the metadata is still 0.1.0**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_project_metadata.py::test_project_version_is_0_1_1_release_candidate -q
```

Expected: `0.1.0 != 0.1.1`.

- [ ] **Step 3: Bump the single source and release-facing examples**

Set:

```toml
[project]
version = "0.1.1"
```

Change only real development/release examples in `DEVELOPMENT.md` and `RELEASING.md` from `0.1.0` /
`v0.1.0` to `0.1.1` / `v0.1.1`. Leave isolated fixture builders under `tests/` unchanged.

In `scripts/verify_distribution.py::verify_installed`, remove `("--version",)` from the generic help
loop and verify it explicitly:

```python
installed_version = importlib.metadata.version("pebra")
version_result = _run_cli("--version", cwd=cwd)
if (
    version_result.returncode != 0
    or not version_result.stdout.startswith(f"PEBRA {installed_version} ")
):
    raise DistributionVerificationError(
        f"installed CLI reported the wrong version: {version_result.stdout.strip()}"
    )
```

- [ ] **Step 4: Run Milestone 1 focused and full verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli_version.py tests/unit/test_cli_help.py tests/integration/test_tui_app.py tests/unit/test_project_metadata.py tests/unit/test_distribution_verifier.py e2e/smoke/test_cli_discovery.py e2e/features/agent/test_agent_init_safety.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\python.exe -m pytest e2e/utils/tests e2e/external/utils/tests e2e/test_boundary_discipline.py e2e/smoke e2e/features/agent -q
.\.venv\Scripts\nox.exe -s tests lint dev-package
$repo = (Resolve-Path -LiteralPath .).Path
$dist = [System.IO.Path]::GetFullPath((Join-Path $repo 'dist'))
if ((Split-Path -Parent $dist) -ne $repo) { throw "unsafe dist path: $dist" }
if (Test-Path -LiteralPath $dist) { Remove-Item -Recurse -Force -LiteralPath $dist }
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\twine.exe check dist\*
.\.venv\Scripts\python.exe scripts/verify_distribution.py archives dist
.\.venv\Scripts\python.exe scripts/verify_distribution.py release-tag v0.1.1
git diff --check
```

Expected: all focused/full lanes pass, including every fast E2E component except the explicitly deferred
A/B directory; the wheel and sdist are `0.1.1`; archive, installed-wheel/dev-package, and tag verification
pass. Task 12 runs the complete A/B aggregate after production integration, per the maintainer's required
sequence.

- [ ] **Step 5: Commit the versioned candidate**

```powershell
git add pyproject.toml RELEASING.md scripts/verify_distribution.py tests/unit/test_project_metadata.py tests/unit/test_distribution_verifier.py
git commit -m "chore: prepare pebra 0.1.1"
```

### STOP FOR REVIEW 1 — 0.1.1 candidate

Report all Milestone 0–1 commits, focused subprocess E2E, full tests/lint, TUI help behavior, README
commands, wheel/sdist names, installed-wheel evidence, and `v0.1.1` tag validation. Confirm that typed gate
contracts and later Agent Integration V2 work are not in the release candidate. Do not push, tag, or
publish without maintainer approval.

---

## Milestone 2 — Publish The Verified 0.1.1 Bytes

### Task 5: Close repository settings, CI, TestPyPI, and PyPI gates

**Files:**
- No source files change in this milestone.

**Interfaces:**
- Consumes: the reviewed `0.1.1` commit, existing `release.yml`, Trusted Publishers, and protected
  `testpypi` / `pypi` environments.
- Produces: annotated `v0.1.1`, byte-identical TestPyPI/PyPI artifacts, and immutable GitHub release.
- Stops: on any settings gap, unavailable version, failed CI job, failed smoke, digest mismatch, or
  candidate-byte change.

- [ ] **Step 1: Prove the version is unused and repository settings are release-capable**

Run read-only checks:

```powershell
gh api repos/Rajioba1/pebra/environments/pypi
gh api repos/Rajioba1/pebra/environments/testpypi
gh api repos/Rajioba1/pebra --jq '{default_branch,visibility}'
gh api -H 'X-GitHub-Api-Version: 2026-03-10' repos/Rajioba1/pebra/immutable-releases
try { Invoke-RestMethod https://pypi.org/pypi/pebra/0.1.1/json; throw '0.1.1 already exists on PyPI' } catch { if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw } }
try { Invoke-RestMethod https://test.pypi.org/pypi/pebra/0.1.1/json; throw '0.1.1 already exists on TestPyPI' } catch { if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw } }
```

Require all of the following before mutation: `pypi` has a required reviewer, `testpypi` and `pypi`
restrict deployments to the intended tag/main policy, both Trusted Publishers name `Rajioba1/pebra`,
`release.yml`, and their exact environment, and immutable releases are enabled. The current observed
baseline has environments but no reviewer rule, and the immutable-releases endpoint reports
`enabled: false`; treat both as open blockers, not assumptions.

GitHub APIs cannot prove PyPI-side publisher configuration. While authenticated as the project owner,
capture evidence from both project publishing pages before tagging:

- `https://test.pypi.org/manage/project/pebra/settings/publishing/` must show GitHub owner `Rajioba1`,
  repository `pebra`, workflow `release.yml`, environment `testpypi`;
- `https://pypi.org/manage/project/pebra/settings/publishing/` must show GitHub owner `Rajioba1`,
  repository `pebra`, workflow `release.yml`, environment `pypi`.

An active publisher is expected because `pebra` already exists. If either page is absent, mismatched, or
only shows an unrelated pending tuple, stop before the tag/workflow and correct it through the package
owner UI. Record screenshots or equivalent authenticated evidence in the review report; prose assertion
alone is not evidence.

- [ ] **Step 2: Configure the two missing safeguards after explicit maintainer approval**

Use the repository settings/API to require GitHub user `Rajioba1` (ID `196587943`) as the `pypi`
environment reviewer with self-review permitted for this single-maintainer repository, and enable
immutable releases. Re-run Step 1 and save the JSON evidence. If the repository plan/visibility does not
support either protection, stop; do not weaken or silently omit the design requirement.

```powershell
$environment = @{
    wait_timer = 0
    prevent_self_review = $false
    reviewers = @(@{ type = "User"; id = 196587943 })
    deployment_branch_policy = @{
        protected_branches = $false
        custom_branch_policies = $true
    }
} | ConvertTo-Json -Depth 5
$environment | gh api --method PUT repos/Rajioba1/pebra/environments/pypi --input -
gh api --method PUT -H 'X-GitHub-Api-Version: 2026-03-10' repos/Rajioba1/pebra/immutable-releases
gh api repos/Rajioba1/pebra/environments/pypi
gh api repos/Rajioba1/pebra/environments/pypi/deployment-branch-policies
gh api -H 'X-GitHub-Api-Version: 2026-03-10' repos/Rajioba1/pebra/immutable-releases
```

The existing branch policy must still contain `main`; the PUT must not erase it.

- [ ] **Step 3: Push the reviewed release commit and require three-OS CI**

```powershell
git status --short
git branch --show-current
$releaseCommit = (git rev-parse HEAD).Trim()
git push origin main
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $ciRun = gh run list --repo Rajioba1/pebra --workflow ci.yml --branch main --limit 20 --json databaseId,headSha,url | ConvertFrom-Json | Where-Object headSha -eq $releaseCommit | Select-Object -First 1
    $securityRun = gh run list --repo Rajioba1/pebra --workflow security.yml --branch main --limit 20 --json databaseId,headSha,url | ConvertFrom-Json | Where-Object headSha -eq $releaseCommit | Select-Object -First 1
    if ($ciRun -and $securityRun) { break }
    Start-Sleep -Seconds 5
}
if (-not $ciRun -or -not $securityRun) { throw "CI runs for $releaseCommit were not found" }
gh run watch $ciRun.databaseId --repo Rajioba1/pebra --exit-status
gh run watch $securityRun.databaseId --repo Rajioba1/pebra --exit-status
gh run view $ciRun.databaseId --repo Rajioba1/pebra --json url,headSha,conclusion,jobs
gh run view $securityRun.databaseId --repo Rajioba1/pebra --json url,headSha,conclusion,jobs
```

Require the CI run for the exact release commit to pass tests, lint, package validation, installed-wheel
checks on Ubuntu/Windows/macOS, CodeGraph, RCA degradation, Playwright, and secret scanning. Record the run
URLs and every required job conclusion. `ci.yml` and the separate `security.yml` must both match
`$releaseCommit`; a green run for an older commit is not evidence.

- [ ] **Step 4: Create and push the annotated release tag**

```powershell
git tag -a v0.1.1 -m "PEBRA 0.1.1"
git push origin v0.1.1
git rev-parse 'v0.1.1^{commit}'
git rev-parse origin/main
```

Expected: both commit IDs are identical. Never move or reuse the tag.

- [ ] **Step 5: Start the build-once release workflow**

```powershell
gh workflow run release.yml --repo Rajioba1/pebra --ref main -f release_tag=v0.1.1
gh run list --repo Rajioba1/pebra --workflow release.yml --event workflow_dispatch --limit 1
```

Record the run ID. Wait for `build-candidate` and `publish-testpypi` to succeed and for `publish-pypi` to
pause at the protected `pypi` environment. Do not approve production yet.

- [ ] **Step 6: Verify and smoke-test the exact TestPyPI candidate**

Download the workflow artifact, verify checksums, query TestPyPI digests, and install from TestPyPI:

```powershell
$releaseCommit = git rev-parse 'v0.1.1^{commit}'
$releaseRun = gh run list --repo Rajioba1/pebra --workflow release.yml --event workflow_dispatch --limit 20 --json databaseId,headSha,url | ConvertFrom-Json | Where-Object headSha -eq $releaseCommit | Sort-Object databaseId -Descending | Select-Object -First 1
if (-not $releaseRun) { throw "release run for commit $releaseCommit was not found" }
$runId = $releaseRun.databaseId
$repo = (Resolve-Path -LiteralPath .).Path
$tempRoot = (Resolve-Path -LiteralPath $env:TEMP).Path
$releaseWork = [System.IO.Path]::GetFullPath((Join-Path $tempRoot 'pebra-0.1.1-release'))
if ((Split-Path -Parent $releaseWork) -ne $tempRoot) { throw "unsafe release work path: $releaseWork" }
if (Test-Path -LiteralPath $releaseWork) { Remove-Item -Recurse -Force -LiteralPath $releaseWork }
New-Item -ItemType Directory -Path $releaseWork | Out-Null
$candidate = Join-Path $releaseWork 'candidate'
$testJson = Join-Path $releaseWork 'testpypi-0.1.1.json'
$testDownload = Join-Path $releaseWork 'testpypi-download'
$smoke = Join-Path $releaseWork 'testpypi-smoke'
gh run download $runId --repo Rajioba1/pebra --name release-candidate --dir $candidate
.\.venv\Scripts\python.exe scripts/verify_distribution.py verify-checksums (Join-Path $candidate 'dist') (Join-Path $candidate 'release/SHA256SUMS')
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    try {
        Invoke-WebRequest https://test.pypi.org/pypi/pebra/0.1.1/json -OutFile $testJson -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq 11) { throw }
        Start-Sleep -Seconds 5
    }
}
.\.venv\Scripts\python.exe scripts/verify_distribution.py index-digests (Join-Path $candidate 'dist') $testJson
.\.venv\Scripts\python.exe -m venv $smoke
$smokePython = Join-Path $smoke 'Scripts/python.exe'
& $smokePython -m pip download --no-deps --index-url https://test.pypi.org/simple/ --dest $testDownload pebra==0.1.1
$testWheel = Get-ChildItem -LiteralPath $testDownload -Filter 'pebra-0.1.1-*.whl' | Select-Object -First 1
if (-not $testWheel) { throw 'TestPyPI wheel was not downloaded' }
& $smokePython -m pip install --index-url https://pypi.org/simple/ $testWheel.FullName
& $smokePython -m pebra --version
& $smokePython -m pebra --help
& $smokePython -m pebra help tui
$oldPath = $env:PATH
try {
    $env:PATH = (Join-Path $smoke 'Scripts') + [System.IO.Path]::PathSeparator + $oldPath
    & $smokePython (Join-Path $repo 'scripts/verify_distribution.py') installed
} finally {
    $env:PATH = $oldPath
}
```

The TestPyPI download is `--no-deps`; dependencies resolve only from production PyPI when installing the
downloaded wheel, avoiding dependency-confusion selection across two indexes. If any command or digest
fails, reject production. If candidate bytes must change, bump to a new patch version; never reuse
`0.1.1`.

- [ ] **Step 7: Approve production and verify PyPI/GitHub bytes**

Approve the pending `pypi` deployment in GitHub only after Step 6 passes. Wait for `publish-pypi` and
`create-github-release`, then run:

```powershell
$productionJson = Join-Path $releaseWork 'pypi-0.1.1.json'
$releaseAssets = Join-Path $releaseWork 'release-assets'
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    try {
        Invoke-WebRequest https://pypi.org/pypi/pebra/0.1.1/json -OutFile $productionJson -ErrorAction Stop
        break
    } catch {
        if ($attempt -eq 11) { throw }
        Start-Sleep -Seconds 5
    }
}
.\.venv\Scripts\python.exe scripts/verify_distribution.py index-digests (Join-Path $candidate 'dist') $productionJson
gh release download v0.1.1 --repo Rajioba1/pebra --dir $releaseAssets
.\.venv\Scripts\python.exe scripts/verify_distribution.py verify-checksums $releaseAssets (Join-Path $releaseAssets 'SHA256SUMS')
gh release view v0.1.1 --repo Rajioba1/pebra --json url,tagName,isImmutable
```

Expected: TestPyPI, PyPI, and GitHub release assets match the same candidate digests and the GitHub release
is immutable.

### STOP FOR REVIEW 2 — 0.1.1 published

Report settings evidence, exact release commit/tag, CI URL and conclusions, release run URL, TestPyPI
smoke output, candidate/index digests, PyPI verification, and immutable GitHub release URL. Do not begin
post-release Agent Integration V2 work without maintainer approval.

---

## Milestone 3 — Gate And Candidate Contracts

### Task 6: Single candidate-binding algorithm constant

**Files:**
- Create: `pebra/core/candidate_binding_contract.py`
- Modify: `pebra/adapters/candidate_binding.py:15-35`
- Modify: `pebra/adapters/gate_check_adapter.py:460-480`
- Modify: `pebra/adapters/enforcement_capability.py:50-79`
- Modify: `pebra/cli/gate_hook.py:32-44`
- Modify: `pebra/app/accept_risk_controller.py:27-38`
- Test: `tests/unit/test_candidate_binding.py`
- Test: `tests/unit/test_gate_hook.py`
- Test: `tests/unit/test_enforcement_capability.py`
- Test: `tests/unit/test_accept_risk_controller.py`

**Interfaces:**
- Produces: `CANDIDATE_BINDING_ALGORITHM: Final[str]` importable by core, app, adapters, and CLI.
- Preserves: the exact external value `sha256-normalized-content-v1`.

- [ ] **Step 1: Write a failing single-source regression**

Add a test that imports the public constant and verifies every public handshake emits it:

```python
from pebra.core.candidate_binding_contract import CANDIDATE_BINDING_ALGORITHM


def test_gate_hook_capability_uses_candidate_binding_contract(capsys):
    args = build_parser().parse_args(["gate-hook", "--capabilities"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["candidate_binding_protocol"] == (
        CANDIDATE_BINDING_ALGORITHM
    )
```

Add corresponding assertions to candidate-binding and approval tests:

```python
def test_patch_binding_uses_public_algorithm_constant(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("old\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch"

    binding = candidate_binding.binding_for_patch(tmp_path, patch)

    assert binding is not None
    assert binding["algorithm"] == CANDIDATE_BINDING_ALGORITHM
```

In `test_accept_risk_controller.py`, replace the fixture's algorithm literal with
`CANDIDATE_BINDING_ALGORITHM`; the existing successful and invalid-algorithm tests then prove approval
uses the same contract.

- [ ] **Step 2: Run the focused tests and confirm the constant is missing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_candidate_binding.py tests/unit/test_gate_hook.py tests/unit/test_enforcement_capability.py tests/unit/test_accept_risk_controller.py -q
```

Expected: collection fails because `pebra.core.candidate_binding_contract` does not exist.

- [ ] **Step 3: Add the core constant and replace production literals**

Create:

```python
"""Candidate identity values shared across trust-boundary layers."""

from typing import Final

CANDIDATE_BINDING_ALGORITHM: Final[str] = "sha256-normalized-content-v1"
```

Replace every production occurrence returned by:

```powershell
rg -n 'sha256-normalized-content-v1' pebra -g '*.py'
```

with the imported constant. After the edit, that command must return only the defining core module.

- [ ] **Step 4: Verify and commit the binding contract**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_candidate_binding.py tests/unit/test_gate_hook.py tests/unit/test_enforcement_capability.py tests/unit/test_accept_risk_controller.py -q
git add pebra/core/candidate_binding_contract.py pebra/adapters/candidate_binding.py pebra/adapters/gate_check_adapter.py pebra/adapters/enforcement_capability.py pebra/cli/gate_hook.py pebra/app/accept_risk_controller.py tests/unit/test_candidate_binding.py tests/unit/test_gate_hook.py tests/unit/test_enforcement_capability.py tests/unit/test_accept_risk_controller.py
git commit -m "refactor: single-source candidate binding protocol"
```

### Task 7: Typed, versioned gate decision contract

**Files:**
- Create: `pebra/core/gate_contract.py`
- Modify: `pebra/adapters/gate_check_adapter.py:46-72`
- Modify: `pebra/cli/gate_check.py:1-56`
- Modify: `pebra/cli/gate_hook.py:45-64`
- Modify: `pebra/app/candidate_apply_controller.py:87-107`
- Create: `docs/GATE_CONTRACT.md`
- Create: `tests/unit/test_gate_contract.py`
- Modify: `tests/unit/test_gate_check.py`
- Modify: `tests/unit/test_gate_hook.py`
- Modify: `tests/unit/test_candidate_apply_controller.py`

**Interfaces:**
- Produces: `GatePermission`, `GateTier`, `GATE_SCHEMA_VERSION`, and `ALLOWED_PERMISSION_TIERS`.
- Changes: `GateDecision.as_dict()` adds `schema_version: 1` while preserving all existing keys.
- Preserves: string compatibility because `StrEnum` members compare as strings.

- [ ] **Step 1: Write failing contract tests**

Create `tests/unit/test_gate_contract.py` with complete coverage:

```python
def test_gate_contract_declares_every_tier_once():
    declared = {tier for tiers in ALLOWED_PERMISSION_TIERS.values() for tier in tiers}
    assert declared == set(GateTier)


@pytest.mark.parametrize(
    ("permission", "tier"),
    [
        (permission, tier)
        for permission, tiers in ALLOWED_PERMISSION_TIERS.items()
        for tier in tiers
    ],
)
def test_declared_pairs_construct(permission, tier):
    decision = GateDecision(permission, tier)
    assert decision.as_dict()["schema_version"] == GATE_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("permission", "tier"),
    [
        (permission, tier)
        for permission in GatePermission
        for tier in GateTier
        if tier not in ALLOWED_PERMISSION_TIERS[permission]
    ],
)
def test_every_undeclared_pair_is_rejected(permission, tier):
    with pytest.raises(ValueError, match="undeclared gate permission/tier pair"):
        GateDecision(permission, tier)


def test_experiment_positive_control_is_not_a_production_tier():
    assert "positive_control" not in {tier.value for tier in GateTier}
```

Add a documentation test that asserts one Markdown row for every allowed pair:

```python
def test_gate_contract_document_has_exact_allowed_pair_set():
    body = (Path(__file__).parents[2] / "docs" / "GATE_CONTRACT.md").read_text(encoding="utf-8")
    documented = set(re.findall(
        r"^\| `(allow|deny|ask)` \| `([^`]+)` \|",
        body,
        flags=re.MULTILINE,
    ))
    expected = {
        (permission.value, tier.value)
        for permission, tiers in ALLOWED_PERMISSION_TIERS.items()
        for tier in tiers
    }
    assert documented == expected
```

- [ ] **Step 2: Run the contract tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_gate_contract.py -q
```

Expected: collection fails because the core contract does not exist.

- [ ] **Step 3: Implement the core enums and allowed matrix**

Create `pebra/core/gate_contract.py`:

```python
from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping


class GatePermission(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class GateTier(StrEnum):
    PASS = "pass"
    FAIL_OPEN = "fail_open"
    MUST_CONSULT = "must_consult"
    CANDIDATE_UNVERIFIABLE = "candidate_unverifiable"
    CANDIDATE_UNBOUND = "candidate_unbound"
    CANDIDATE_MISMATCH = "candidate_mismatch"
    CANDIDATE_INCOMPLETE = "candidate_incomplete"
    CONSULTED = "consulted"
    CONSULTED_REVISE = "consulted_revise"
    CONSULTED_PREREQUISITE = "consulted_prerequisite"
    CONSULTED_REVIEW = "consulted_review"
    CONSULTED_REVIEW_UNAVAILABLE = "consulted_review_unavailable"


GATE_SCHEMA_VERSION: Final[int] = 1
ALLOWED_PERMISSION_TIERS: Final[Mapping[GatePermission, frozenset[GateTier]]] = MappingProxyType({
    GatePermission.ALLOW: frozenset({GateTier.PASS, GateTier.FAIL_OPEN, GateTier.CONSULTED}),
    GatePermission.ASK: frozenset({GateTier.CONSULTED_REVIEW}),
    GatePermission.DENY: frozenset(set(GateTier) - {
        GateTier.PASS,
        GateTier.FAIL_OPEN,
        GateTier.CONSULTED,
        GateTier.CONSULTED_REVIEW,
    }),
})
```

- [ ] **Step 4: Normalize and validate GateDecision**

Retain `GateDecision` in the adapter but type it with the core enums:

```python
@dataclass(frozen=True)
class GateDecision:
    permission: GatePermission | str
    tier: GateTier | str
    reason: str | None = None
    warn: str | None = None
    matched_assessment_id: str | None = None

    def __post_init__(self) -> None:
        permission = GatePermission(self.permission)
        tier = GateTier(self.tier)
        if tier not in ALLOWED_PERMISSION_TIERS[permission]:
            raise ValueError(f"undeclared gate permission/tier pair: {permission}/{tier}")
        object.__setattr__(self, "permission", permission)
        object.__setattr__(self, "tier", tier)

    def as_dict(self, *, include_host_metadata: bool = False) -> dict[str, Any]:
        payload = {
            "schema_version": GATE_SCHEMA_VERSION,
            "permission": self.permission.value,
            "tier": self.tier.value,
            "reason": self.reason,
            "warn": self.warn,
        }
        if include_host_metadata:
            payload["matched_assessment_id"] = self.matched_assessment_id
        return payload
```

Change production call sites to enum members. Update the one test fake using `ask/ask` to
`ask/consulted_review`. Candidate application must compare against
`GatePermission.ALLOW` and `GateTier.CONSULTED`.

- [ ] **Step 5: Document the stable envelope and diagnostic matrix**

Write `docs/GATE_CONTRACT.md` with:

- schema version and JSON fields;
- the full allowed `(permission, tier)` table;
- the rule that hosts act only on permission;
- `deny > ask > allow` within PEBRA's emitted decisions;
- the preserved `allow/fail_open` infrastructure policy;
- the same-OS-identity threat limitation;
- the rule that a gate deny/ask for the attempted candidate overrides an earlier advisory proceed.

Do not describe tiers as independent host commands and do not add `gate-check --self-test`.

- [ ] **Step 6: Run Milestone 3 verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_gate_contract.py tests/unit/test_gate_check.py tests/unit/test_gate_hook.py tests/unit/test_candidate_apply_controller.py -q
.\.venv\Scripts\nox.exe -s tests lint
git diff --check
```

Expected: focused and full suites pass; import-linter keeps every contract; E2E-fast retains the existing
boundary behavior.

- [ ] **Step 7: Commit the gate contract**

```powershell
git add pebra/core/gate_contract.py pebra/adapters/gate_check_adapter.py pebra/cli/gate_check.py pebra/cli/gate_hook.py pebra/app/candidate_apply_controller.py tests/unit/test_gate_contract.py tests/unit/test_gate_check.py tests/unit/test_gate_hook.py tests/unit/test_candidate_apply_controller.py
git add -f docs/GATE_CONTRACT.md
git commit -m "feat: define the gate decision contract"
```

### Task 8: Prove the production gate envelope over the E2E process boundary

**Files:**
- Create: `e2e/utils/tests/test_gate_contract_cli.py`

**Interfaces:**
- Consumes: the existing subprocess-only `cli_harness.gate_check()` without changing experiment
  infrastructure.
- Proves: production emits schema 1 with the existing permission/tier behavior.
- Defers: consumer-side schema rejection and every A/B runner/test change to Milestone 6.

- [ ] **Step 1: Write the failing real-CLI envelope test**

Create `e2e/utils/tests/test_gate_contract_cli.py`:

```python
from __future__ import annotations

from e2e.utils import cli_harness


def test_gate_check_real_cli_emits_schema_one_envelope(tmp_path):
    payload = cli_harness.gate_check({}, db=tmp_path / "missing.db", consult_only=True)

    assert payload == {
        "schema_version": 1,
        "permission": "allow",
        "tier": "pass",
        "reason": None,
        "warn": None,
        "matched_assessment_id": None,
    }


def test_gate_hook_capabilities_emit_candidate_binding_protocol():
    result = subprocess.run(
        [sys.executable, "-m", "pebra", "gate-hook", "--capabilities"],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["candidate_binding_protocol"] == (
        "sha256-normalized-content-v1"
    )
```

Import `json`, `subprocess`, and `sys` in the new test module. The literal binding value here is the
external consumer's expected wire value; E2E must not import the production constant.

- [ ] **Step 2: Run Milestone 3 E2E acceptance**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/utils/tests/test_gate_contract_cli.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
git diff --check
```

Expected: the real production subprocess emits schema 1 with the unchanged `allow/pass` missing-store
behavior, and no E2E module imports PEBRA.

- [ ] **Step 3: Commit the process-boundary acceptance test**

```powershell
git add e2e/utils/tests/test_gate_contract_cli.py
git commit -m "test: verify gate schema over cli boundary"
```

### STOP FOR REVIEW 3

Report all three commits, the complete enum/matrix, real subprocess envelope evidence, documentation
coverage, and focused gate-contract E2E verification. Confirm that no decision math or fail-open path
changed. The full A/B alignment and experiment suite remain intentionally deferred to the final
milestone.

---

## Milestone 4 — Always-loaded Claude Guidance And Inspection

### Task 9: Add the concise Claude rule and semantic projection tests

**Files:**
- Modify: `pebra/cli/agent_init.py`
- Modify: `tests/unit/test_agent_init.py`
- Modify: `README.md:85-120`

**Interfaces:**
- Produces: `.claude/rules/pebra-safe-edit.md` for the Claude target.
- Preserves: the detailed canonical `SKILL.md` and Codex managed `AGENTS.md` block.

- [ ] **Step 1: Write failing Claude-rule and projection tests**

```python
_CLAUDE_RULE_REL = Path(".claude/rules/pebra-safe-edit.md")
_OBLIGATIONS = (
    "assess before",
    "mismatched or incomplete candidate",
    "deny or ask",
    "human sanction",
    "verify and record",
)


def test_claude_writes_always_loaded_non_negotiables(tmp_path):
    assert _run("claude", tmp_path) == 0
    body = (tmp_path / _CLAUDE_RULE_REL).read_text(encoding="utf-8").lower()
    for obligation in _OBLIGATIONS:
        assert obligation in body


def test_full_host_skills_are_byte_identical(tmp_path):
    assert _run("claude", tmp_path) == 0
    claude = (tmp_path / _SKILL_REL).read_bytes()
    assert _run("codex", tmp_path) == 0
    codex = (tmp_path / ".agents/skills/pebra-safe-edit/SKILL.md").read_bytes()
    assert claude == codex
```

Add a test iterating the live decision enum:

```python
def test_detailed_protocol_names_every_live_decision():
    for decision in Decision:
        assert decision.value in agent_init._PROTOCOL_BODY
```

- [ ] **Step 2: Run the focused test and verify the rule is absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py -q
```

Expected: the new Claude rule test fails.

- [ ] **Step 3: Generate and include the managed Claude rule**

Add one rendered constant:

```python
_CLAUDE_RULE_MD = """\
# PEBRA safe-edit non-negotiables

1. Assess before every significant edit, rename, or delete.
2. Never apply a mismatched or incomplete candidate; apply only the exact assessed candidate.
3. A PEBRA gate deny or ask overrides an earlier advisory proceed for the attempted candidate.
4. Never create, claim, or answer your own human sanction.
5. After application, verify and record the outcome.
"""
```

The Claude write plan includes this fully managed file and the existing skill. Do not edit a user's
`CLAUDE.md`, use an `@` import, or add a symlink.

- [ ] **Step 4: Update README support documentation and verify**

Document the Claude skill, unconditional rule, verified optional hook, Codex managed block/skill, and
best-effort hook. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py tests/unit/test_cli_help.py -q
git diff --check
```

Expected: all projection, preservation, and documentation tests pass.

- [ ] **Step 5: Commit the always-loaded projection**

```powershell
git add pebra/cli/agent_init.py tests/unit/test_agent_init.py README.md
git commit -m "feat: add always-loaded Claude safety rules"
```

### Task 10: Add `agent-init --check --json`

**Files:**
- Modify: `pebra/cli/agent_init.py`
- Modify: `tests/unit/test_agent_init.py`
- Modify: `README.md`
- Create: `e2e/features/agent/test_agent_init_inspection.py`

**Interfaces:**
- Produces: `pebra agent-init --target {claude,codex} --check [--json]`.
- Produces JSON keys: `command`, `target`, `protocol_version`, `gate_schema_version`, `files`, `hook`,
  `declared_support`, and `effective_enforcement`.
- Preserves: normal initialization behavior when `--check` is absent.

- [ ] **Step 1: Write the read-only state matrix tests**

Parameterize file states (`absent`, `current`, `modified`) and hook states (`absent`, `exact`,
`conflicting`, `malformed`). Capture a recursive snapshot before and after every check:

```python
def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_agent_init_check_json_is_non_mutating(tmp_path, capsys):
    _run("claude", tmp_path)
    before = _tree_snapshot(tmp_path)
    args = build_parser().parse_args([
        "agent-init", "--target", "claude", "--repo-root", str(tmp_path), "--check", "--json",
    ])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == "claude"
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert _tree_snapshot(tmp_path) == before
```

Mock language/enforcement probes so unit tests never shell out. Add a parser/runner test proving `--json`
without `--check` returns `2` and writes nothing:

```python
def test_agent_init_json_requires_check(tmp_path, capsys):
    args = build_parser().parse_args([
        "agent-init", "--target", "claude", "--repo-root", str(tmp_path), "--json",
    ])
    assert args.func(args) == 2
    assert "--json requires --check" in capsys.readouterr().err
assert _tree_snapshot(tmp_path) == {}
```

Lock hook-state precedence with unit cases for:

- exact owned entry alone → `exact`;
- exact owned entry plus unrelated or lookalike entries → `exact`;
- exact owned entry plus a PEBRA-shaped conflicting entry → `conflicting`;
- exact command under the wrong matcher → `conflicting`;
- expected matcher with a malformed hook list → `conflicting`;
- expected matcher with only a different or substring-lookalike command → `absent`;
- malformed document/`hooks`/`PreToolUse` containers → `malformed`.

Inspection and deletion use different predicates: only `is_managed_hook_entry()` authorizes replacement;
the broader conflict classifier is read-only and must never delete an entry.

- [ ] **Step 2: Run focused tests and verify the flags are absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py -q
```

Expected: argparse rejects `--check` and `--json`.

- [ ] **Step 3: Register check mode and inspect rendered expectations**

Add `--check` and `--json` (`dest="as_json"`) to the existing parser. Reuse the normal renderers to compare
expected content, so check and write paths cannot drift:

```python
def _file_state(path: Path, expected: str) -> str:
    if not path.exists():
        return "absent"
    try:
        actual = path.read_text(encoding="utf-8")
    except OSError:
        return "modified"
    return "current" if actual == expected else "modified"
```

Hook inspection parses without writing and returns `malformed` for every shape rejected by Task 1,
`exact` only for the shared exact predicate, `absent` when no PreToolUse entry exists, and `conflicting`
for a non-owned PEBRA-shaped candidate. Classify all entries before returning: any conflicting candidate
wins over an exact entry; otherwise exact wins over unrelated entries; otherwise return absent.

A PEBRA-shaped conflicting candidate is either an entry containing the exact command
`pebra gate-hook` but not matching the complete owned structure, or an expected-matcher entry whose
`hooks` value is structurally malformed. A different command—including a substring lookalike—is
unrelated even when it uses the same matcher. This rule depends on Task 1's compatibility invariant:
`HOOK_COMMAND` cannot change until known legacy PEBRA commands are explicitly added to a tested migration
predicate. Never infer legacy ownership from a substring.

- [ ] **Step 4: Reuse measured capability reporting without making it authorization**

In check mode only, lazily call the existing language capability probe and
`enforcement_capability.probe`. Embed the selected host's result as `effective_enforcement`. Do not cache
it, persist it, or use it to authorize an edit.

Set `PROTOCOL_VERSION = 1` beside the canonical generated protocol and include
`GATE_SCHEMA_VERSION` from the core contract.

- [ ] **Step 5: Render human and JSON output from one payload**

Human output lists each path/state, hook state, declared support, and effective mode. JSON uses sorted,
indented output. Both paths return `0` even for `modified`, `conflicting`, or `malformed`; those are
inspection results, not CLI crashes. README documentation must state that check mode never repairs;
normal `agent-init` refreshes fully managed instruction content and installs a missing current hook, but
does not claim to repair a conflicting or legacy hook. Conflict resolution requires a deliberate tested
migration or user action.

- [ ] **Step 6: Prove materialization and non-mutation over the process boundary**

Create `e2e/features/agent/test_agent_init_inspection.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _agent_init(root: Path, target: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", target,
            "--repo-root", str(root), *extra,
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("target", ("claude", "codex"))
def test_installed_host_check_is_real_cli_non_mutating(tmp_path, target):
    installed = _agent_init(tmp_path, target, "--with-hook")
    assert installed.returncode == 0
    if target == "claude":
        rule = tmp_path / ".claude/rules/pebra-safe-edit.md"
        body = rule.read_text(encoding="utf-8")
        for obligation in ("assess", "mismatched", "deny", "human sanction", "verify"):
            assert obligation in body.lower()

    before = _snapshot(tmp_path)
    checked = _agent_init(tmp_path, target, "--check", "--json")
    assert checked.returncode == 0
    payload = json.loads(checked.stdout)
    assert payload["protocol_version"] == 1
    assert payload["gate_schema_version"] == 1
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert payload["hook"]["state"] == "exact"
    assert _snapshot(tmp_path) == before
```

Add a second test with malformed existing hook JSON. `--check --json` must return `0`, report
`hook.state == "malformed"`, and leave the recursive byte snapshot unchanged for both targets. Add a
third real-process test proving `--json` without `--check` returns `2` and creates no files for both
targets.

- [ ] **Step 7: Run Milestone 4 E2E acceptance and commit inspection**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py -q
.\.venv\Scripts\python.exe -m pytest e2e/features/agent/test_agent_init_safety.py e2e/features/agent/test_agent_init_inspection.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
git diff --check
git add pebra/cli/agent_init.py tests/unit/test_agent_init.py README.md e2e/features/agent/test_agent_init_inspection.py
git commit -m "feat: inspect agent integration state"
```

### STOP FOR REVIEW 4

Report the Claude rule, complete check schema, all state-matrix evidence, and proof that check mode creates
or modifies no files. Do not proceed without maintainer approval.

---

## Milestone 5 — Two-host Registry And Conformance

### Task 11: Single-source minimal host facts

**Files:**
- Create: `pebra/core/agent_hosts.py`
- Modify: `pebra/cli/agent_init.py`
- Modify: `pebra/adapters/enforcement_capability.py`
- Modify: `pebra/cli/capabilities.py`
- Modify: `tests/unit/test_agent_init.py`
- Modify: `tests/unit/test_enforcement_capability.py`
- Modify: `tests/unit/test_capabilities_cli.py`
- Create: `tests/unit/test_agent_host_conformance.py`
- Modify: `tests/unit/test_distribution_verifier.py`
- Create: `e2e/features/agent/test_agent_host_conformance.py`
- Modify: `scripts/verify_distribution.py`
- Modify: `README.md`

**Interfaces:**
- Produces: minimal `HostSpec` and ordered `AGENT_HOSTS` for exactly `claude` and `codex`.
- Preserves: explicit host renderers; the registry stores facts, not executable plugins.

- [ ] **Step 1: Write failing registry/conformance tests**

Create tests asserting:

```python
def test_parser_choices_match_registry():
    parser = build_parser()
    action = next(
        action
        for action in parser._subparsers._group_actions[0].choices["agent-init"]._actions
        if action.dest == "target"
    )
    assert tuple(action.choices) == tuple(AGENT_HOSTS)


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_every_host_materializes_the_safe_edit_protocol(target, tmp_path):
    assert _run(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    assert (tmp_path / spec.skill_path).read_text(encoding="utf-8") == agent_init._SKILL_MD


def test_no_unverified_runtime_is_declared():
    assert tuple(AGENT_HOSTS) == ("claude", "codex")
```

Add tests that installation and capability observation agree on exact path, matcher, command, and support
tier for every host. Mark the README support table rows with stable ``<!-- agent-host:<target> -->``
comments, then assert exact coverage:

```python
def test_readme_support_rows_match_registry():
    body = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")
    declared = set(re.findall(r"<!-- agent-host:([a-z0-9_-]+) -->", body))
    assert declared == set(AGENT_HOSTS)
```

Do not stop at marker coverage. Parse the table row following each marker and assert that it contains
the registry's exact machine-readable `declared_support` value. This makes both a missing host and a
stale guarantee tier fail CI; marker-only equality would still allow the README to claim enforcement
while the registry says `best_effort`. Human-facing display labels remain README content rather than
registry data.

- [ ] **Step 2: Run tests and confirm the registry is absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_host_conformance.py -q
```

Expected: collection fails because `pebra.core.agent_hosts` does not exist.

- [ ] **Step 3: Add the immutable two-host registry**

Create `pebra/core/agent_hosts.py`:

```python
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
```

Do not add display names, invocation commands, callbacks, or future-host metadata to `HostSpec`. Those
values have no production consumer in this implementation. The five fields above are the complete
registry surface.

- [ ] **Step 4: Replace duplicated stable facts without hiding host behavior**

Use `tuple(AGENT_HOSTS)` for parser choices and capability display order. Use each `HostSpec` for paths,
matchers, and declared support. Keep explicit Claude/Codex rendering branches in `agent_init.py`; do not add
callbacks, dynamic imports, entry points, or plugin discovery to the registry.

Keep MCP as a separate advisory capability surface, not an `agent-init` host.

- [ ] **Step 5: Add the semantic conformance matrix**

For every host, verify:

- pre-edit assessment obligation;
- bounded `revise_safer` reassessment;
- trusted-human escalation;
- exact-candidate application;
- post-edit verification;
- outcome recording;
- full skill byte identity;
- correct instruction surface;
- exact hook ownership and honest support tier;
- advisory/best-effort surfaces never claim verified enforcement.

Use one registry-derived projection test so adding a target without all evidence fails CI:

```python
_SEMANTIC_TOKENS = (
    "pebra assess",
    "revise_safer",
    "trusted human or host",
    "apply-candidate --assessment-id",
    "pebra verify",
    "record-outcome",
)


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_host_projection_contains_complete_protocol(target, tmp_path):
    assert _run(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    skill = (tmp_path / spec.skill_path).read_text(encoding="utf-8")
    for token in _SEMANTIC_TOKENS:
        assert token in skill


@pytest.mark.parametrize("target", tuple(AGENT_HOSTS))
def test_installed_hook_matches_registry_and_probe(target, tmp_path):
    assert _run_with_hook(target, tmp_path) == 0
    spec = AGENT_HOSTS[target]
    hook_path = tmp_path / spec.hook_path
    assert enforcement_capability._hook_installed(hook_path, spec.hook_matcher)
```

- [ ] **Step 6: Prove both registry targets over the E2E process boundary**

Create `e2e/features/agent/test_agent_host_conformance.py`. Parameterize `claude` and `codex`, launch
`python -m pebra agent-init --target <target> --repo-root <tmp> --with-hook`, then launch the same target
with `--check --json`. Assert:

```python
assert installed.returncode == 0
assert checked.returncode == 0
payload = json.loads(checked.stdout)
assert payload["target"] == target
assert {item["state"] for item in payload["files"]} == {"current"}
assert payload["hook"]["state"] == "exact"
assert payload["declared_support"] == expected_support
```

For each materialized full skill, assert the six semantic tokens from Task 11 are present. For Claude,
also assert `.claude/rules/pebra-safe-edit.md` exists; for Codex, assert the PEBRA managed block appears
inside `AGENTS.md` without deleting pre-existing sentinel content.

- [ ] **Step 7: Extend installed-wheel verification to agent integration**

In `scripts/verify_distribution.py::verify_installed`, use `_run_cli` inside its existing temporary
directory to initialize and inspect both targets from the installed wheel:

```python
for target in ("claude", "codex"):
    repo_root = cwd / f"agent-{target}"
    installed = _run_cli(
        "agent-init", "--target", target, "--repo-root", str(repo_root), "--with-hook", cwd=cwd,
    )
    if installed.returncode != 0:
        raise DistributionVerificationError(
            f"installed agent-init failed for {target}: {installed.stderr.strip()}"
        )
    before = {
        path.relative_to(repo_root).as_posix(): path.read_bytes()
        for path in repo_root.rglob("*")
        if path.is_file()
    }
    checked = _run_cli(
        "agent-init", "--target", target, "--repo-root", str(repo_root),
        "--check", "--json", cwd=cwd,
    )
    if checked.returncode != 0:
        raise DistributionVerificationError(
            f"installed agent-init check failed for {target}: {checked.stderr.strip()}"
        )
    payload = json.loads(checked.stdout)
    if payload["target"] != target or payload["hook"]["state"] != "exact":
        raise DistributionVerificationError(f"installed agent-init state mismatch for {target}")
    after = {
        path.relative_to(repo_root).as_posix(): path.read_bytes()
        for path in repo_root.rglob("*")
        if path.is_file()
    }
    if after != before:
        raise DistributionVerificationError("installed agent-init check mutated repository state")
```

Extend `test_installed_verifier_exercises_console_script` to assert the verifier source contains both
`"agent-init"` and `"--check"`; the real fresh-venv `verify_distribution installed` run remains the
authoritative behavioral proof.

- [ ] **Step 8: Run Milestone 5 E2E, local, and distribution verification**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/features/agent/test_agent_init_safety.py e2e/features/agent/test_agent_init_inspection.py e2e/features/agent/test_agent_host_conformance.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint dev-package
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\twine.exe check dist\*
.\.venv\Scripts\python.exe scripts/verify_distribution.py archives dist
git diff --check
```

Expected: every target passes through the subprocess boundary, generated instruction and inspection
behavior work from the installed wheel, check mode stays byte-for-byte non-mutating, and no new runtime
dependency or package data is required.

- [ ] **Step 9: Commit the registry and conformance matrix**

```powershell
git add pebra/core/agent_hosts.py pebra/cli/agent_init.py pebra/adapters/enforcement_capability.py pebra/cli/capabilities.py tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py tests/unit/test_capabilities_cli.py tests/unit/test_agent_host_conformance.py tests/unit/test_distribution_verifier.py e2e/features/agent/test_agent_host_conformance.py scripts/verify_distribution.py README.md
git commit -m "refactor: single-source agent host support"
```

### STOP FOR REVIEW 5 — Production integration checkpoint

Report the Milestone 5 commit, registry contents, focused subprocess E2E, conformance coverage, and
installed-wheel evidence. Explicitly confirm:

- only Claude and Codex are declared;
- fail-open behavior and decision math are unchanged;
- check mode is non-mutating;
- malformed user configuration is preserved;
- no plugin engine, updater, inbox, queue, symlink projection, or provider-branded target was added.

Do not push or begin the full experiment alignment until the maintainer approves this production
checkpoint.

---

## Milestone 6 — Agent A/B Alignment And Aggregate E2E

### Task 12: Align the existing experiment with the completed production contracts

**Files:**
- Modify: `e2e/utils/cli_harness.py:1-207`
- Modify: `e2e/utils/tests/test_gate_contract_cli.py`
- Modify: `e2e/experiments/agent_ab/runners/agent_loop.py:270-288`
- Modify: `e2e/experiments/agent_ab/runners/run_pair.py:1056-1096,1404-1445`
- Modify: `e2e/experiments/agent_ab/tests/test_run_pair.py`
- Modify: `e2e/experiments/agent_ab/tests/test_preflight.py`
- Modify: `e2e/experiments/agent_ab/tests/test_blinding.py`
- Modify: `e2e/experiments/agent_ab/tests/test_run_trial.py`
- Modify: `e2e/experiments/agent_ab/tests/test_agent_loop.py`
- Modify: `e2e/experiments/agent_ab/tests/test_write_gate.py`
- Modify: `e2e/experiments/agent_ab/README.md`

**Interfaces:**
- Consumes: the production schema-1 envelope proved over the real CLI boundary in Milestone 3.
- Changes: test doubles that represent the real CLI response use the complete production-shaped gate
  envelope; incompatible schema is experiment-fatal while ordinary gate infrastructure failures remain
  fail-open; the runner names and documents its experiment-only positive-control tier.
- Preserves: arm definitions, randomization, task corpus, prompts, model calls, consult-only treatment,
  blinding, oracle/scoring rules, telemetry semantics, and the exact model-facing write result.
- Does not run: the paid/provider-backed live assay (`nox -s e2e-ab`) without separate maintainer
  authorization and its existing environment gates.

- [ ] **Step 1: Write failing consumer-side gate-schema tests**

Extend `e2e/utils/tests/test_gate_contract_cli.py` with a complete valid host envelope and invalid cases:

```python
_VALID_GATE = {
    "schema_version": 1,
    "permission": "allow",
    "tier": "pass",
    "reason": None,
    "warn": None,
    "matched_assessment_id": None,
}


@pytest.mark.parametrize(
    "payload",
    (
        [],
        {},
        {**_VALID_GATE, "schema_version": 2},
        {**_VALID_GATE, "permission": "continue"},
        {**_VALID_GATE, "tier": "unknown"},
        {**_VALID_GATE, "permission": "allow", "tier": "must_consult"},
        {**_VALID_GATE, "permission": "deny", "tier": "positive_control"},
        {**_VALID_GATE, "reason": 7},
        {**_VALID_GATE, "warn": []},
        {key: value for key, value in _VALID_GATE.items() if key != "matched_assessment_id"},
    ),
)
def test_gate_envelope_rejects_unsupported_or_malformed_payload(payload):
    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"])
```

Add to `e2e/experiments/agent_ab/tests/test_write_gate.py`:

```python
def test_incompatible_gate_contract_aborts_without_writing(tmp_path):
    def incompatible(_event):
        raise cli_harness.GateContractError("unsupported gate contract schema")

    setup = SimpleNamespace(repo_path=tmp_path, gate_check_backend=incompatible)
    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        agent_loop._gated_write({"path": "a.cs", "content": "hi"}, setup)

    assert not (tmp_path / "a.cs").exists()
```

Import `pytest` and `e2e.utils.cli_harness` in that test module. Retain
`test_gated_write_fails_open_on_backend_error`; together the tests distinguish protocol incompatibility
from an ordinary unavailable gate.

Add runner regressions proving protocol compatibility is checked before any provider/model trial begins:

```python
def test_incompatible_gate_contract_aborts_before_provider_setup(monkeypatch, treatment_setup):
    from e2e.experiments.agent_ab.runners import run_gate

    treatment_setup.gate_check_backend = lambda _event: (_ for _ in ()).throw(
        cli_harness.GateContractError("unsupported gate contract schema")
    )
    monkeypatch.setattr(run_gate, "check_gate", lambda: None)
    monkeypatch.setattr(
        run_pair,
        "_load_config",
        lambda: pytest.fail("provider configuration must not begin"),
    )

    with pytest.raises(cli_harness.GateContractError, match="gate contract"):
        run_pair._invoke_subject_agent(treatment_setup, treatment_setup.spec, seed=1)
```

Add a companion test where the probe raises an ordinary `cli_harness.CLIError`: the preflight returns
and the test's sentinel `_load_config` is reached. That locks the intentional distinction between a
fatal protocol incompatibility and a fail-open infrastructure outage without making a provider call.

Expected initial failure: `_validate_gate_envelope` does not exist. The real schema-one subprocess test
from Milestone 3 stays unchanged and must continue to pass.

- [ ] **Step 2: Make the experiment harness a schema-1 consumer**

Add this consumer contract to `cli_harness.py`; do not import PEBRA, because the E2E boundary must remain
an external process boundary:

```python
SUPPORTED_GATE_SCHEMA_VERSION = 1
_GATE_PERMISSION_TIERS = {
    "allow": frozenset({"pass", "fail_open", "consulted"}),
    "ask": frozenset({"consulted_review"}),
    "deny": frozenset({
        "must_consult",
        "candidate_unverifiable",
        "candidate_unbound",
        "candidate_mismatch",
        "candidate_incomplete",
        "consulted_revise",
        "consulted_prerequisite",
        "consulted_review_unavailable",
    }),
}


class GateContractError(CLIError):
    """The gate wire payload is incompatible with this experiment consumer."""


def _validate_gate_envelope(payload: object, cmd: list[str]) -> dict:
    if not isinstance(payload, dict):
        raise GateContractError(f"command {cmd!r} returned a non-object gate contract")
    schema = payload.get("schema_version")
    if type(schema) is not int or schema != SUPPORTED_GATE_SCHEMA_VERSION:
        raise GateContractError(
            f"command {cmd!r} returned unsupported gate contract schema "
            f"{schema!r}"
        )
    permission = payload.get("permission")
    tier = payload.get("tier")
    if permission not in _GATE_PERMISSION_TIERS:
        raise GateContractError(f"command {cmd!r} returned an invalid gate contract permission")
    if tier not in _GATE_PERMISSION_TIERS[permission]:
        raise GateContractError(f"command {cmd!r} returned an invalid gate contract tier pair")
    for field in ("reason", "warn", "matched_assessment_id"):
        value = payload.get(field)
        if field not in payload or (value is not None and not isinstance(value, str)):
            raise GateContractError(
                f"command {cmd!r} returned an invalid gate contract {field}"
            )
    return payload
```

Change `gate_check()` to return `_validate_gate_envelope(_parse_json_stdout(proc.stdout, cmd), cmd)` and
update its docstring to name the complete versioned envelope. This single path covers calibration,
preflight, and trial execution. Allow unknown extra keys within schema 1; additions are compatible unless
the schema version changes.

In `agent_loop.py`, import `cli_harness` and split the catch:

```python
    except cli_harness.GateContractError:
        raise
    except Exception:  # noqa: BLE001 - ordinary gate infrastructure failure stays fail-open
        decision = {"permission": "allow"}
```

This prevents an incompatible protocol from silently changing the treatment into an allowed write while
preserving the experiment's deliberate fail-open behavior for ordinary gate/runtime outages.

In `run_pair.py`, add a no-write contract probe for real gated arms and invoke it immediately after the
existing `run_gate.check_gate()` authorization check, before `_load_config()`, provider selection, API-key
lookup, or client construction:

```python
def _preflight_gate_contract(setup: ArmSetup) -> None:
    if setup.arm not in _GATE_ARMS:
        return
    try:
        setup.gate_check_backend({})
    except cli_harness.GateContractError:
        raise
    except Exception:  # noqa: BLE001 - availability remains deliberately fail-open
        return


run_gate.check_gate()
_preflight_gate_contract(setup)
cfg = _load_config()["subject"]
```

The empty event is a read-only production `gate-check` query. For gated arms, the backend calls the same
validated `cli_harness.gate_check()` path used by calibration and writes; therefore an unsupported schema
cannot consume model/provider work. Do not run the probe for synthetic controls, and do not reinterpret
ordinary CLI/runtime failure as a fatal contract error.

- [ ] **Step 3: Write the failing experiment compatibility regressions**

Update every mock of `cli_harness.gate_check` in the files above so a mock representing the production
subprocess returns at least:

```python
{
    "schema_version": 1,
    "permission": "allow",
    "tier": "consulted",
    "reason": None,
    "warn": None,
    "matched_assessment_id": None,
}
```

Use the test's existing permission, tier, warning, reason, and matched assessment values where they differ.
Do not require this envelope from fakes injected directly as the already-normalized internal
`gate_check_backend`; those are unit boundaries rather than CLI responses.

Update `test_treatment_gate_check_backend_uses_consult_only` to prove the real-shaped envelope survives
the runner boundary and `consult_only` remains true. Update
`test_allowed_assessment_is_attributed_only_after_write_succeeds` so the internal decision carries
`schema_version: 1` while retaining:

```python
assert result == {"ok": True, "blocked": False, "reason": None}
```

That exact equality is the blinding guarantee: new production metadata never reaches the coding agent.
Keep the companion failed-write test proving no assessment is credited when mutation fails.

Add this positive-control regression to `test_run_pair.py`:

```python
def test_enforced_control_uses_unversioned_experiment_only_tier(tmp_path):
    decision = run_pair._gate_check_backend(
        models.ARM_ENFORCED_CONTROL, tmp_path / "pebra.db",
    )({"tool_name": "Write", "tool_input": {"file_path": "a.py"}})

    assert decision["tier"] == run_pair._EXPERIMENT_ONLY_POSITIVE_CONTROL_TIER
    assert "schema_version" not in decision
```

- [ ] **Step 4: Mark the positive control as synthetic in the runner**

In `run_pair.py`, add and use:

```python
_EXPERIMENT_ONLY_POSITIVE_CONTROL_TIER = "positive_control"
```

Expand `_gate_check_backend`'s docstring to state that the enforced control is deliberately not a
production gate response and therefore carries no production `schema_version`. Do not add this tier to
`GateTier`, `ALLOWED_PERMISSION_TIERS`, or `docs/GATE_CONTRACT.md`. Synthetic sham/control decisions must
not pass through the production-envelope validator.

- [ ] **Step 5: Document the experiment/production boundary**

Update `e2e/experiments/agent_ab/README.md` to state:

- PEBRA treatment calls the real schema-1 gate subprocess with `consult_only=True`;
- the harness rejects an unsupported gate schema before preflight or a trial;
- the runner performs a read-only schema probe after its existing authorization gate but before provider
  configuration or client construction;
- the assay has no trusted human approver, so unresolved review remains conservatively blocked;
- only `{ok, blocked, reason}` is model-facing in every arm;
- `positive_control` is a synthetic experiment label, not a production `GateTier`;
- this alignment changes no arm, prompt, task, outcome metric, or expected treatment effect.

- [ ] **Step 6: Run focused A/B compatibility tests**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/utils/tests/test_gate_contract_cli.py e2e/experiments/agent_ab/tests/test_write_gate.py e2e/experiments/agent_ab/tests/test_run_pair.py::test_treatment_gate_check_backend_uses_consult_only e2e/experiments/agent_ab/tests/test_run_pair.py::test_exact_allowed_candidate_is_bound_for_post_edit_verify e2e/experiments/agent_ab/tests/test_run_pair.py::test_enforced_control_uses_unversioned_experiment_only_tier e2e/experiments/agent_ab/tests/test_agent_loop.py::test_allowed_assessment_is_attributed_only_after_write_succeeds e2e/experiments/agent_ab/tests/test_agent_loop.py::test_failed_write_never_credits_allowed_assessment e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/test_boundary_discipline.py -q
```

Expected: schema-1 data is consumed internally, consult-only and post-write attribution are unchanged,
the model-facing schema remains fixed, positive control remains synthetic, blinding passes, and no E2E
module imports PEBRA.

- [ ] **Step 7: Run the deferred aggregate E2E proof**

```powershell
.\.venv\Scripts\nox.exe -s tests lint e2e-fast
git diff --check
```

Expected: the complete deterministic experiment suite and aggregate fast E2E lane pass. Compare test
counts and skipped-test reasons with the pre-milestone baseline. Any change to arm definitions, prompts,
task fixtures, oracle labels, scoring, provider calls, or model-facing tool schemas is a blocker requiring
a new experiment-design review.

Do not run `nox -s e2e-ab`; it is the live provider-backed assay and remains protected by
`E2E_AB_RUN=1`, `E2E_EXTERNAL=1`, and the provider key.

- [ ] **Step 8: Commit the experiment alignment**

```powershell
git add e2e/utils/cli_harness.py e2e/utils/tests/test_gate_contract_cli.py e2e/experiments/agent_ab/runners/agent_loop.py e2e/experiments/agent_ab/runners/run_pair.py e2e/experiments/agent_ab/tests/test_run_pair.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_run_trial.py e2e/experiments/agent_ab/tests/test_agent_loop.py e2e/experiments/agent_ab/tests/test_write_gate.py e2e/experiments/agent_ab/README.md
git commit -m "test: align agent experiment with gate schema"
```

- [ ] **Step 9: Run hosted cross-platform proof after explicit push approval**

After the maintainer authorizes pushing, push `main` and require the installed-wheel/test matrix to pass
on Ubuntu, Windows, and macOS. Record workflow run URLs and job conclusions. Do not tag, publish, or add a
runtime-support claim while any required job is missing or failing.

### STOP FOR REVIEW 6 — Final checkpoint

Report every milestone commit, each milestone's focused subprocess E2E evidence, the aggregate A/B and
`e2e-fast` results, distribution evidence, and hosted three-OS results. Explicitly confirm:

- production gate schema 1 is consumed by the experiment;
- treatment still uses the real consult-only gate and only successful writes receive attribution;
- model-facing results remain exactly `{ok, blocked, reason}` in every arm;
- `positive_control` remains experiment-local;
- no decision math, fail-open behavior, arm, prompt, task corpus, oracle, scoring rule, or live model call
  changed;
- the live provider-backed assay was not launched without separate authorization.

Runtime expansion requires a new approved spec based on a real host-loading experiment.

## Deferred And Non-goals

- Changing `HOOK_COMMAND` or automatically migrating a legacy PEBRA hook signature. Any future change
  requires a separate approved migration spec with known legacy signatures and deduplication tests.
- Adding another agent runtime, an agent plugin registry, dynamic host callbacks, or invocation metadata.
