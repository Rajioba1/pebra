# PEBRA Agent Integration V2 Design

## Goal

Make PEBRA's existing safe-edit engine reliably discoverable, inspectable, and semantically consistent
across coding-agent hosts without weakening its pre-act gate or turning PEBRA into a multi-agent
orchestrator.

## Context

PEBRA already has the stronger enforcement architecture: one read-only gate decision primitive wraps the
Claude hook, the best-effort Codex surface, candidate application, and the agent A/B experiment. The
generated Claude skill and Codex skill/`AGENTS.md` block also derive from one protocol body. The remaining
problems are narrower:

- hook installation can overwrite malformed user configuration and can delete a lookalike user hook;
- gate permissions and diagnostic tiers are untyped string literals;
- Claude's full protocol is skill-loaded rather than accompanied by a concise always-loaded rule;
- installation is write-only, so users cannot inspect absent, current, modified, conflicting, or
  malformed integration state;
- host facts are duplicated and will drift if a third runtime is added without one registry and one
  conformance matrix.

The `0.1.1` design owns the first two destructive configuration fixes as release blockers. This document
defines the contract and portability work that follows.

## Locked decisions

1. **Permission is the host contract; tier is diagnostic.** Hosts branch on `allow`, `deny`, or `ask`.
   A tier explains why that permission was returned and must not independently choose a host action.
2. **Preserve fail-open infrastructure policy.** Graph, Git, or store infrastructure failure continues to
   return `allow/fail_open` with a visible warning and degraded capability status. This design does not
   change that availability tradeoff.
3. **Guidance complements enforcement.** Always-loaded instructions remain advisory. Verified hooks remain
   the only host-enforced pre-act surface.
4. **Materialize content directly.** Do not depend on symlinks, pointer files, imports, or a self-updater.
5. **No runtime is supported by file presence alone.** A support claim requires a declared host record,
   generated artifacts, load-path evidence, and semantic conformance tests.
6. **Runtime names, not model brands.** DeepSeek is not a target unless a concrete host with a stable
   instruction or hook contract is identified and verified.
7. **No new runtime in the first implementation plan.** Build the safety, contract, inspection, and
   conformance foundation first. Runtime expansion receives a separate experiment and review.

## Architecture

### 1. Validation-first agent initialization

`agent-init` first renders and validates every intended destination in memory, then writes only after the
entire plan succeeds. Existing malformed JSON or structurally invalid hook containers are errors, not
empty configuration. A validation error leaves all existing files byte-identical and creates no new
instruction or hook files.

The ownership predicate compares the complete PEBRA-owned hook structure: expected matcher, one command
hook with `type == "command"`, and `command == "pebra gate-hook"`. It is shared by installation and
capability observation. Lookalike commands and conflicting structures are preserved and later reported by
the inspection surface.

This guarantees atomicity with respect to validation. It does not promise an operating-system transaction
across multiple files if an unrelated write fails after successful preflight.

### 2. Typed gate contract

Create a dependency-free core contract containing:

- `GatePermission`: `allow`, `deny`, `ask`;
- `GateTier`: `pass`, `fail_open`, `must_consult`, `candidate_unverifiable`, `candidate_unbound`,
  `candidate_mismatch`, `candidate_incomplete`, `consulted`, `consulted_revise`,
  `consulted_prerequisite`, `consulted_review`, and `consulted_review_unavailable`;
- `GATE_SCHEMA_VERSION = 1`;
- the allowed permission/tier matrix.

`GateDecision` normalizes values into those enums and rejects an undeclared pair. Its JSON output includes
`schema_version`, string permission/tier values, reason, warning, and optional host metadata. Host shims
continue to act only on permission.

`docs/GATE_CONTRACT.md` documents the JSON envelope, allowed pairs, fail-open policy, precedence, and
threat boundary. A test derives every table row from the live allowed-pair matrix, so adding a tier without
updating the documentation fails CI. There is no `gate-check --self-test`; unit and conformance tests cover
the pure contract, while installation state belongs to `agent-init --check --json`.

### 3. Candidate-binding constant

Move `sha256-normalized-content-v1` into one public, dependency-free core constant. Candidate binding,
gate checking, approval, hook handshakes, enforcement capability checks, and tests import that value.
Agent-facing instructions need not expose the algorithm name; they state the semantic obligation to apply
the exact assessed candidate.

### 4. Always-loaded Claude non-negotiables

The Claude target writes a concise, fully managed `.claude/rules/pebra-safe-edit.md` in addition to the
existing detailed skill. An unconditional rule is loaded every session without modifying or duplicating a
user-owned `CLAUDE.md`.

The rule contains only five obligations:

1. assess before significant edits;
2. never apply a mismatched or incomplete candidate;
3. a gate `deny` or `ask` overrides an earlier advisory `proceed` for the attempted candidate;
4. an agent never creates or answers its own human sanction;
5. verify and record after application.

The full workflow remains in `SKILL.md`. Codex continues to receive its managed `AGENTS.md` block and
materialized skill. Tests compare semantic obligations across the Claude rule, Claude skill, Codex block,
and Codex skill without requiring those differently shaped documents to be byte-identical. The two full
skills remain byte-identical.

### 5. Non-mutating installation inspection

`pebra agent-init --target <host> --check --json` performs no writes and reports:

- each expected file as `absent`, `current`, or `modified`;
- hook configuration as `absent`, `exact`, `conflicting`, or `malformed`;
- the host's declared support tier;
- the effective enforcement result obtained from the existing capability adapter;
- protocol and gate schema versions.

Human-readable `--check` output renders the same payload. `--json` is valid only with `--check`.
Inspection never repairs state; rerunning normal `agent-init` is the explicit repair action.

### 6. Minimal host registry and conformance matrix

Before a third runtime is introduced, create one immutable registry that declares only stable host facts:

- target name and display name;
- instruction and skill destinations;
- verified hook path and matcher, when one exists;
- declared guarantee tier;
- interactive and headless invocation notes.

Host-specific rendering functions stay explicit; the registry is not a plugin framework. Parser choices,
installation inspection, capability reporting, CLI ordering, and support-matrix tests read the registry.

Registry-parameterized tests prove every declared target receives the same semantic protocol obligations,
full skills are materialized byte-identically, installation and capability observation agree on hook
ownership, and advisory targets never claim enforcement.

## Error handling and trust boundaries

- Malformed user configuration is reported with its path and expected shape; it is never replaced.
- Modified managed content is reported by `--check`; normal initialization may overwrite only files
  explicitly designated as fully managed.
- User-owned `AGENTS.md` content remains outside PEBRA's marked block and is preserved.
- Conflicting hooks are preserved and reported rather than guessed to be PEBRA-owned.
- The hook remains non-blocking on infrastructure failure, surfaces a warning, and never treats
  `capabilities` as candidate authorization.
- An agent running with the same OS identity can edit its instruction and hook files. The design improves
  honest configuration and drift detection; it is not a sandbox against an adversarial local process.

## Milestones and review gates

### Milestone 0 — 0.1.1 release safety

Implement validation-first writes and exact hook ownership with malformed/no-partial-write regressions.
Stop for review before continuing or publishing `0.1.1`.

### Milestone 1 — Gate contract and binding constant

Add typed permissions/tiers, schema version, allowed-pair validation, contract documentation, and the
single candidate-binding constant. Stop for review after focused tests, full tests, lint, and import
contracts pass.

### Milestone 2 — Always-loaded Claude rule and inspection

Add the managed Claude rule, semantic projection tests, and non-mutating `agent-init --check --json`.
Stop for review after proving check mode performs no writes for every state.

### Milestone 3 — Registry and cross-host conformance

Replace duplicated two-host facts with the minimal registry and parameterized conformance matrix. Do not
add a third runtime. Stop for review after installed-wheel and Windows/Ubuntu/macOS CI evidence.

### Deferred runtime expansion

A later spec may add a generic advisory target or individually verified hosts such as OpenCode or Qwen.
Each target must first prove its real instruction-loading convention, Windows materialization, headless
invocation, and honest guarantee tier. Unverified hooks, model-provider targets, and presence-only support
claims remain forbidden.

## Verification requirements

- TDD regressions for malformed JSON and every invalid hook shape.
- Byte-for-byte no-write assertions on validation and check paths.
- Complete enum and allowed-pair coverage.
- Documentation rows derived from live contract values.
- Candidate-binding consumers use the single core constant.
- Claude always-loaded rule and full skills contain the required semantic obligations.
- Full Claude and Codex skills are byte-identical.
- Registry, parser choices, capability order, inspection, and README support declarations cannot drift.
- `nox -s tests lint e2e-fast` passes at every milestone.
- Distribution verification proves all generated templates remain available from an installed wheel.
- Final Ubuntu, Windows, and macOS CI is required before any runtime-support claim or release.

## Non-goals

- Changing decision math, candidate authorization, sanctions, or persistence.
- Replacing the pre-act gate with CI-after-the-fact enforcement.
- A multi-agent inbox, work queue, or coordinator.
- A plugin engine, third-party risk-rule loader, or self-updater.
- Symlink or pointer-file projection.
- Markdown as the canonical assessment ledger.
- Adding Gemini, DeepSeek, OpenCode, Qwen, Kimi, Grok, or any other runtime in this implementation slice.
