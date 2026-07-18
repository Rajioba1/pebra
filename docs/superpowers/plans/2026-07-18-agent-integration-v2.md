# PEBRA Agent Integration V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PEBRA agent initialization validation-safe, give the gate a typed and documented wire contract, add always-loaded Claude non-negotiables and non-mutating installation inspection, prevent Claude/Codex support facts from drifting, then align the existing agent A/B experiment with the completed production behavior.

**Architecture:** Preserve the existing gate and generated protocol, adding small dependency-free core contracts for hook ownership, candidate binding, gate permissions/tiers, and host facts. `pebra agent-init` renders and validates a complete write plan before touching disk; CLI/adapters consume the same core declarations. The agent A/B subprocess harness becomes an explicit consumer of gate schema version 1 and fails before a trial on an incompatible envelope while its treatment, blinding, and model-facing result remain unchanged. This plan adds no third runtime and does not change decision math, sanctions, persistence, or fail-open infrastructure policy.

**Tech Stack:** Python 3.11+, `argparse`, frozen dataclasses, `enum.StrEnum`, JSON, pytest, Ruff, import-linter, nox, GitHub Actions.

## Global Constraints

- Work on `main`; do not create a feature branch unless the maintainer changes this instruction.
- Use test-first development for every behavior change.
- Preserve `allow/fail_open` for graph, Git, store, parse, and unexpected hook-runtime failures.
- Host wrappers branch on `GatePermission`; `GateTier` is diagnostic and cannot independently authorize an edit.
- Preserve the A/B treatment: call the real gate subprocess with `consult_only=True`, attribute an assessment only after a successful write, and expose exactly `{ok, blocked, reason}` to the model in every arm.
- Keep `positive_control` as an experiment-local synthetic label; never add it to the production `GateTier` enum or present it as a versioned production gate response.
- Before each review stop, run the milestone's focused subprocess E2E acceptance tests. Defer the complete deterministic A/B suite and `nox -s e2e-fast` to the final experiment milestone because they are the expensive aggregate proof.
- Never overwrite malformed user configuration or delete a lookalike user hook.
- Never modify user content outside PEBRA's existing managed block.
- Materialize complete skill/rule content; no symlinks, pointer files, external imports, or self-updater.
- Do not add a third agent runtime in this plan.
- Do not push, tag, publish, or continue past a `STOP FOR REVIEW` without maintainer approval.

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
- Produces: `HOOK_COMMAND: str`, `managed_hook_entry(matcher: str) -> dict[str, object]`, and `is_managed_hook_entry(value: object, matcher: str) -> bool`.
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
        '{"hooks": {"PreToolUse": {}}}',
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

Add the Codex variant and a valid-settings preservation test. Validation failure must not create either
instruction files or hook files.

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
def test_agent_init_malformed_hook_is_failure_atomic(tmp_path, target, config_rel, skill_rel):
    config = tmp_path / config_rel
    config.parent.mkdir(parents=True)
    raw = "{broken"
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

from typing import Any

HOOK_COMMAND = "pebra gate-hook"


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
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise AgentInitConfigError(f"{path}: hooks must be an object")
    entries = hooks.get("PreToolUse")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise AgentInitConfigError(f"{path}: hooks.PreToolUse must be an array")
    kept = [entry for entry in entries if not is_managed_hook_entry(entry, matcher)]
    hooks["PreToolUse"] = [*kept, managed_hook_entry(matcher)]
    return json.dumps(data, indent=2) + "\n"
```

Add pure renderers for the skill and managed `AGENTS.md` content. `_plan_agent_init` must read and render
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

## Milestone 1 — Gate And Candidate Contracts

### Task 2: Single candidate-binding algorithm constant

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

CANDIDATE_BINDING_ALGORITHM: Final = "sha256-normalized-content-v1"
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

### Task 3: Typed, versioned gate decision contract

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
        (GatePermission.ALLOW, GateTier.PASS),
        (GatePermission.ALLOW, GateTier.FAIL_OPEN),
        (GatePermission.ALLOW, GateTier.CONSULTED),
        (GatePermission.ASK, GateTier.CONSULTED_REVIEW),
        (GatePermission.DENY, GateTier.MUST_CONSULT),
    ],
)
def test_declared_pairs_construct(permission, tier):
    decision = GateDecision(permission, tier)
    assert decision.as_dict()["schema_version"] == GATE_SCHEMA_VERSION


def test_undeclared_pair_is_rejected():
    with pytest.raises(ValueError, match="undeclared gate permission/tier pair"):
        GateDecision(GatePermission.ALLOW, GateTier.MUST_CONSULT)


def test_experiment_positive_control_is_not_a_production_tier():
    assert "positive_control" not in {tier.value for tier in GateTier}
```

Add a documentation test that asserts one Markdown row for every allowed pair:

```python
def test_gate_contract_document_covers_every_allowed_pair():
    body = (Path(__file__).parents[2] / "docs" / "GATE_CONTRACT.md").read_text(encoding="utf-8")
    for permission, tiers in ALLOWED_PERMISSION_TIERS.items():
        for tier in tiers:
            assert f"| `{permission.value}` | `{tier.value}` |" in body
```

The document contains no tier outside `GateTier`:

```python
def test_gate_contract_document_has_no_undeclared_tier():
    body = (Path(__file__).parents[2] / "docs" / "GATE_CONTRACT.md").read_text(encoding="utf-8")
    documented = set(re.findall(
        r"^\| `(?:allow|deny|ask)` \| `([^`]+)` \|",
        body,
        flags=re.MULTILINE,
    ))
    assert documented == {tier.value for tier in GateTier}
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


GATE_SCHEMA_VERSION: Final = 1
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

- [ ] **Step 6: Run Milestone 1 verification**

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

### Task 4: Prove the production gate envelope over the E2E process boundary

**Files:**
- Create: `e2e/utils/tests/test_gate_contract_cli.py`

**Interfaces:**
- Consumes: the existing subprocess-only `cli_harness.gate_check()` without changing experiment
  infrastructure.
- Proves: production emits schema 1 with the existing permission/tier behavior.
- Defers: consumer-side schema rejection and every A/B runner/test change to Milestone 4.

- [ ] **Step 1: Write the failing real-CLI envelope test**

Create `e2e/utils/tests/test_gate_contract_cli.py`:

```python
from __future__ import annotations

from e2e.utils import cli_harness


def test_gate_check_real_cli_emits_schema_one_envelope(tmp_path):
    payload = cli_harness.gate_check({}, db=tmp_path / "missing.db", consult_only=True)

    assert payload["schema_version"] == 1
    assert payload["permission"] == "allow"
    assert payload["tier"] == "pass"
```

- [ ] **Step 2: Run Milestone 1 E2E acceptance**

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

### STOP FOR REVIEW 1

Report all three commits, the complete enum/matrix, real subprocess envelope evidence, documentation
coverage, and focused gate-contract E2E verification. Confirm that no decision math or fail-open path
changed. The full A/B alignment and experiment suite remain intentionally deferred to the final
milestone.

---

## Milestone 2 — Always-loaded Claude Guidance And Inspection

### Task 5: Add the concise Claude rule and semantic projection tests

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

### Task 6: Add `agent-init --check --json`

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
otherwise.

- [ ] **Step 4: Reuse measured capability reporting without making it authorization**

In check mode only, lazily call the existing language capability probe and
`enforcement_capability.probe`. Embed the selected host's result as `effective_enforcement`. Do not cache
it, persist it, or use it to authorize an edit.

Set `PROTOCOL_VERSION = 1` beside the canonical generated protocol and include
`GATE_SCHEMA_VERSION` from the core contract.

- [ ] **Step 5: Render human and JSON output from one payload**

Human output lists each path/state, hook state, declared support, and effective mode. JSON uses sorted,
indented output. Both paths return `0` even for `modified`, `conflicting`, or `malformed`; those are
inspection results, not CLI crashes.

- [ ] **Step 6: Prove materialization and non-mutation over the process boundary**

Create `e2e/features/agent/test_agent_init_inspection.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _agent_init(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "pebra", "agent-init", "--target", "claude",
            "--repo-root", str(root), *extra,
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_claude_rule_and_check_are_real_cli_non_mutating(tmp_path):
    installed = _agent_init(tmp_path, "--with-hook")
    assert installed.returncode == 0
    rule = tmp_path / ".claude/rules/pebra-safe-edit.md"
    body = rule.read_text(encoding="utf-8")
    for obligation in ("assess", "mismatched", "deny", "trusted human", "verify"):
        assert obligation in body.lower()

    before = _snapshot(tmp_path)
    checked = _agent_init(tmp_path, "--check", "--json")
    assert checked.returncode == 0
    payload = json.loads(checked.stdout)
    assert payload["protocol_version"] == 1
    assert payload["gate_schema_version"] == 1
    assert {item["state"] for item in payload["files"]} == {"current"}
    assert payload["hook"]["state"] == "exact"
    assert _snapshot(tmp_path) == before
```

Add a second test with malformed existing hook JSON. `--check --json` must return `0`, report
`hook.state == "malformed"`, and leave the recursive byte snapshot unchanged.

- [ ] **Step 7: Run Milestone 2 E2E acceptance and commit inspection**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_init.py tests/unit/test_enforcement_capability.py -q
.\.venv\Scripts\python.exe -m pytest e2e/features/agent/test_agent_init_safety.py e2e/features/agent/test_agent_init_inspection.py e2e/test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
git diff --check
git add pebra/cli/agent_init.py tests/unit/test_agent_init.py README.md e2e/features/agent/test_agent_init_inspection.py
git commit -m "feat: inspect agent integration state"
```

### STOP FOR REVIEW 2

Report the Claude rule, complete check schema, all state-matrix evidence, and proof that check mode creates
or modifies no files. Do not proceed without maintainer approval.

---

## Milestone 3 — Two-host Registry And Conformance

### Task 7: Single-source stable host facts

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
- Produces: `HostSpec` and ordered `AGENT_HOSTS` for exactly `claude` and `codex`.
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
    display_name: str
    skill_path: str
    instruction_paths: tuple[str, ...]
    hook_path: str
    hook_matcher: str
    declared_support: str
    interactive_invocation: str
    headless_invocation: str | None


AGENT_HOSTS: Final[Mapping[str, HostSpec]] = MappingProxyType({
    "claude": HostSpec(
        display_name="Claude Code",
        skill_path=".claude/skills/pebra-safe-edit/SKILL.md",
        instruction_paths=(".claude/rules/pebra-safe-edit.md",),
        hook_path=".claude/settings.json",
        hook_matcher="Edit|Write|MultiEdit",
        declared_support="configured_enforcing",
        interactive_invocation="claude",
        headless_invocation='claude -p "<prompt>"',
    ),
    "codex": HostSpec(
        display_name="Codex",
        skill_path=".agents/skills/pebra-safe-edit/SKILL.md",
        instruction_paths=("AGENTS.md",),
        hook_path=".codex/hooks.json",
        hook_matcher="apply_patch",
        declared_support="best_effort",
        interactive_invocation="codex",
        headless_invocation='codex exec "<prompt>"',
    ),
})
```

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

For each materialized full skill, assert the six semantic tokens from Task 7 are present. For Claude,
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

- [ ] **Step 8: Run Milestone 3 E2E, local, and distribution verification**

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

### STOP FOR REVIEW 3 — Production integration checkpoint

Report the Milestone 3 commit, registry contents, focused subprocess E2E, conformance coverage, and
installed-wheel evidence. Explicitly confirm:

- only Claude and Codex are declared;
- fail-open behavior and decision math are unchanged;
- check mode is non-mutating;
- malformed user configuration is preserved;
- no plugin engine, updater, inbox, queue, symlink projection, or provider-branded target was added.

Do not push or begin the full experiment alignment until the maintainer approves this production
checkpoint.

---

## Milestone 4 — Agent A/B Alignment And Aggregate E2E

### Task 8: Align the existing experiment with the completed production contracts

**Files:**
- Modify: `e2e/utils/cli_harness.py:1-207`
- Modify: `e2e/utils/tests/test_gate_contract_cli.py`
- Modify: `e2e/experiments/agent_ab/runners/run_pair.py:1056-1096`
- Modify: `e2e/experiments/agent_ab/tests/test_run_pair.py`
- Modify: `e2e/experiments/agent_ab/tests/test_preflight.py`
- Modify: `e2e/experiments/agent_ab/tests/test_blinding.py`
- Modify: `e2e/experiments/agent_ab/tests/test_run_trial.py`
- Modify: `e2e/experiments/agent_ab/tests/test_agent_loop.py`
- Modify: `e2e/experiments/agent_ab/README.md`

**Interfaces:**
- Consumes: the schema-1 validation already enforced by `e2e.utils.cli_harness.gate_check` in Milestone 1.
- Changes: test doubles that represent the real CLI response use the complete production-shaped gate
  envelope; the runner names and documents its experiment-only positive-control tier.
- Preserves: arm definitions, randomization, task corpus, prompts, model calls, consult-only treatment,
  blinding, oracle/scoring rules, telemetry semantics, and the exact model-facing write result.
- Does not run: the paid/provider-backed live assay (`nox -s e2e-ab`) without separate maintainer
  authorization and its existing environment gates.

- [ ] **Step 1: Write failing consumer-side gate-schema tests**

Extend `e2e/utils/tests/test_gate_contract_cli.py`:

```python
@pytest.mark.parametrize(
    "payload",
    (
        [],
        {},
        {"schema_version": 2, "permission": "allow", "tier": "pass"},
        {"schema_version": 1, "permission": "continue", "tier": "pass"},
        {"schema_version": 1, "permission": "allow", "tier": None},
    ),
)
def test_gate_envelope_rejects_unsupported_or_malformed_payload(payload):
    with pytest.raises(cli_harness.CLIError, match="gate contract"):
        cli_harness._validate_gate_envelope(payload, ["pebra", "gate-check"])
```

Expected initial failure: `_validate_gate_envelope` does not exist. The real schema-one subprocess test
from Milestone 1 stays unchanged and must continue to pass.

- [ ] **Step 2: Make the experiment harness a schema-1 consumer**

Add this consumer contract to `cli_harness.py`; do not import PEBRA, because the E2E boundary must remain
an external process boundary:

```python
SUPPORTED_GATE_SCHEMA_VERSION = 1
_GATE_PERMISSIONS = frozenset({"allow", "deny", "ask"})


def _validate_gate_envelope(payload: object, cmd: list[str]) -> dict:
    if not isinstance(payload, dict):
        raise CLIError(f"command {cmd!r} returned a non-object gate contract")
    if payload.get("schema_version") != SUPPORTED_GATE_SCHEMA_VERSION:
        raise CLIError(
            f"command {cmd!r} returned unsupported gate contract schema "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("permission") not in _GATE_PERMISSIONS:
        raise CLIError(f"command {cmd!r} returned an invalid gate contract permission")
    if not isinstance(payload.get("tier"), str):
        raise CLIError(f"command {cmd!r} returned an invalid gate contract tier")
    return payload
```

Change `gate_check()` to return `_validate_gate_envelope(_parse_json_stdout(proc.stdout, cmd), cmd)` and
update its docstring to name the complete versioned envelope. This single path covers calibration,
preflight, and trial execution.

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
- the assay has no trusted human approver, so unresolved review remains conservatively blocked;
- only `{ok, blocked, reason}` is model-facing in every arm;
- `positive_control` is a synthetic experiment label, not a production `GateTier`;
- this alignment changes no arm, prompt, task, outcome metric, or expected treatment effect.

- [ ] **Step 6: Run focused A/B compatibility tests**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/utils/tests/test_gate_contract_cli.py e2e/experiments/agent_ab/tests/test_run_pair.py::test_treatment_gate_check_backend_uses_consult_only e2e/experiments/agent_ab/tests/test_run_pair.py::test_exact_allowed_candidate_is_bound_for_post_edit_verify e2e/experiments/agent_ab/tests/test_run_pair.py::test_enforced_control_uses_unversioned_experiment_only_tier e2e/experiments/agent_ab/tests/test_agent_loop.py::test_allowed_assessment_is_attributed_only_after_write_succeeds e2e/experiments/agent_ab/tests/test_agent_loop.py::test_failed_write_never_credits_allowed_assessment e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/test_boundary_discipline.py -q
```

Expected: schema-1 data is consumed internally, consult-only and post-write attribution are unchanged,
the model-facing schema remains fixed, positive control remains synthetic, blinding passes, and no E2E
module imports PEBRA.

- [ ] **Step 7: Run the deferred aggregate E2E proof**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e/experiments/agent_ab/tests -q
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
git add e2e/utils/cli_harness.py e2e/utils/tests/test_gate_contract_cli.py e2e/experiments/agent_ab/runners/run_pair.py e2e/experiments/agent_ab/tests/test_run_pair.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_run_trial.py e2e/experiments/agent_ab/tests/test_agent_loop.py e2e/experiments/agent_ab/README.md
git commit -m "test: align agent experiment with gate schema"
```

- [ ] **Step 9: Run hosted cross-platform proof after explicit push approval**

After the maintainer authorizes pushing, push `main` and require the installed-wheel/test matrix to pass
on Ubuntu, Windows, and macOS. Record workflow run URLs and job conclusions. Do not tag, publish, or add a
runtime-support claim while any required job is missing or failing.

### STOP FOR REVIEW 4 — Final checkpoint

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
