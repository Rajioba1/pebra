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

1. **Permission is the host wire contract; candidate disposition is the product meaning.** Preserve the
   external values `allow`, `deny`, and `ask`, but name them internally `CONTINUE`, `RETURN_CANDIDATE`,
   and `REQUEST_HUMAN`. A restrictive result holds only the exact attempted candidate; it never rejects
   the human's goal or tells the agent to disobey the user. A tier explains the disposition and must not
   independently choose a host action.
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
8. **The agent A/B experiment is a versioned gate-contract consumer and runs last.** Its treatment
   continues to call the real `pebra gate-check --consult-only` subprocess and act only on permission.
   It must reject an unsupported gate schema before running a trial, keep the model-facing write-result
   fields fixed at `{ok, blocked, reason}`, and preserve conservative no-human execution and post-write
   attribution. Adding candidate-bound mathematics to `reason` intentionally changes treatment content,
   so aligned trials use a new experiment design hash and run ID and must not resume or pool outcomes
   from the earlier treatment. `positive_control` remains an experiment-local synthetic tier and is not
   added to the production `GateTier` enum.

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

`pebra gate-hook` is an installed compatibility invariant for this design, not an implementation detail.
Agent Integration V2 does not change it. A future command change must first define an explicit allowlist
of legacy PEBRA-owned hook signatures and prove, for each supported host, that initialization replaces one
legacy entry with exactly one current entry while preserving unrelated hooks. Do not add an unverified
metadata field to host hook JSON merely to anticipate that migration.

This guarantees atomicity with respect to validation. It does not promise an operating-system transaction
across multiple files if an unrelated write fails after successful preflight.

### 2. Typed gate contract

Create a dependency-free core contract containing:

- `GatePermission.CONTINUE = "allow"`, `GatePermission.RETURN_CANDIDATE = "deny"`, and
  `GatePermission.REQUEST_HUMAN = "ask"`;
- `GateTier`: `pass`, `fail_open`, `must_consult`, `candidate_unverifiable`, `candidate_unbound`,
  `candidate_mismatch`, `candidate_incomplete`, `consulted`, `consulted_revise`,
  `consulted_prerequisite`, `consulted_review`, and `consulted_review_unavailable`;
- a nullable `risk_summary` object containing the exact matched assessment's `decision`,
  `expected_loss`, `benefit`, and `rau`;
- `GATE_SCHEMA_VERSION = 1`;
- the allowed permission/tier matrix.

`GateDecision` normalizes values into those enums and rejects an undeclared pair. Its JSON output includes
`schema_version`, string permission/tier values, reason, warning, nullable `risk_summary`, and optional
host metadata. A risk summary is emitted only when a fresh assessment is bound to the exact attempted
candidate and all three numeric values are finite. Accepted integers and floats are normalized to floats;
booleans, non-finite values, and oversized integers that cannot be represented as floats are rejected
locally. It is omitted as a unit for missing, stale, unbound, unverifiable, mismatched, or malformed
evidence; the gate never attaches plausible-looking stale numbers.
Internally, a non-null summary requires the exact matched assessment identifier (`asm_<positive-int>`),
even when an untrusted serialized surface omits host-only attribution.
The contract also validates the summary decision against the permission/tier pair: `allow/consulted`
accepts only `proceed`; `deny/consulted_revise` only `revise_safer`;
`deny/consulted_prerequisite` only `inspect_first`/`test_first`; `ask/consulted_review` only
`ask_human`; `deny/consulted_review` only `reject`; and
`deny/consulted_review_unavailable` only `ask_human`. Non-consulted pairs cannot carry a
summary, and every restrictive permission requires a nonblank actionable reason.

Restrictive exact-candidate reasons use neutral, actionable language: “this exact candidate is held—not
your requested goal,” followed, when available, by the assessment decision, expected loss, benefit, RAU,
and the next valid action. If exact persisted scores are missing, partial, or non-finite, the gate remains
restrictive and states “risk summary unavailable”; it never crashes, fails open, fabricates, or partially
prints values. Persisted decisions are parsed through the six-value `Decision` enum; only explicit
`proceed` can return `allow/consulted`. A null, unknown, or corrupt value returns `allow/fail_open` with a
visible data-integrity warning and without a risk summary or assessment attribution. Bound candidate
application still refuses that result because its tier is not `consulted`.
`revise_safer`, `inspect_first`, and `test_first` return the candidate for the named work.
`ask_human` returns the candidate with instructions for the existing bound sanction and reassessment
workflow (`pebra accept-risk --apply`) on interactive installed hooks only when persisted candidate replay
metadata is structurally valid: status `available`, exact algorithm `sha256-candidate-replay-v1`, and a
64-character lowercase hexadecimal digest. Missing, malformed, or unavailable replay keeps the candidate held and requests reassessment
or another route; it must not promise an inapplicable approval command. A consult-only surface states that
no trusted approver is available without exposing product/experiment identity. `reject` returns the
candidate and asks the human to choose a different candidate or route;
persisted `reject` is not eligible for the current sanction workflow. The persisted six-value assessment
`Decision` enum is unchanged for storage and learning compatibility; only the gate-facing semantics stop
presenting it as rejection of the user's intent.

Host shims act on permission subject to a tested host-capability projection. Claude supports native
`ask`, but approving that prompt runs the tool without creating PEBRA's exact sanction or reassessment.
Codex `PreToolUse` does not support `ask` at all: emitting it marks the hook failed and continues the tool
call. Therefore both installed hook projections translate `REQUEST_HUMAN` into a blocking `deny` carrying
the candidate-hold reason. For `ask_human`, the reason directs the agent to the bound sanction/reassessment
workflow; for `reject`, it directs the agent to a different candidate or route. Native wire `ask` remains
in the universal contract only for a future trusted adapter that proves an exact PEBRA approval callback.
Installed shims must never silently convert a candidate hold into a warning-only write. `pebra gate-hook`
remains the installed command; this design does not change the locked hook signature.

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
3. a gate candidate hold or bound-human-review request overrides an earlier advisory `proceed` for that
   attempted candidate without cancelling the user's goal;
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
- the effective enforcement result obtained from the existing capability adapter with graph state
  explicitly unverified;
- protocol and gate schema versions.

Human-readable `--check` output renders the same payload. `--json` is valid only with `--check`.
Inspection never repairs state and never invokes CodeGraph: even a pinned CodeGraph status operation may
migrate or write index state. A configured hook therefore reports `graph_unverified_read_only` and is
not candidate-bound in this inspection. `pebra capabilities` remains the separate measured surface and
is not inspection-only. Hook classification is shared by installation inspection and capability
reporting; malformed sibling matcher groups or handlers override an otherwise exact entry, and neither
surface may claim enforcement for a malformed or conflicting config. Redirects observed during managed-
path preflight validation are rejected before writes and are not followed. Inspection reports observed
redirected instruction files as `modified` and observed redirected hook paths as `conflicting`.

The same conservative rule applies to hardlinked managed destination files (`lstat().st_nlink > 1` for
regular files only): initialization aborts before any write, inspection reports instruction files as
`modified` and hook files as `conflicting`, and capability reporting never credits the aliased hook.
Non-descendant path checks fail conservatively rather than raising. User-home hook state is resolved once,
then the settings path is built under that resolved boundary so a symlink or junction home alias cannot
crash or bypass the check. These preflight checks do not provide an OS-level time-of-check/time-of-use
guarantee; a concurrent process running as the same OS identity can swap a path after validation, which is
outside the threat boundary.

Classification validates the selected host's documented hook schema. Omitted or empty matchers are valid
match-all groups, while a present non-string matcher is malformed. Handler lists must be non-empty. Claude
recognizes `command`, `http`, `mcp_tool`, `prompt`, and `agent` with their documented required string fields;
Codex recognizes `command` plus its parsed-but-skipped `prompt` and `agent` compatibility types, and rejects
unknown or Claude-only handler types. This validation remains read-only: exact structural equality through
`is_managed_hook_entry()` is still the sole mutation ownership predicate.

Rerunning normal `agent-init` repairs fully managed instruction content
and installs a missing current hook. With `--with-hook`, an existing malformed or conflicting document
aborts the complete validation-first plan before any instruction or hook write and points the user to
`--check --json`; it never appends into or claims enforcement over that document. Such a hook remains
visible until a deliberate, tested migration or user resolution handles it.

### 6. Minimal host registry and conformance matrix

Create one small immutable registry that declares only stable facts consumed by current production code:

- target key;
- instruction and skill destinations;
- verified hook path and matcher;
- declared guarantee tier.

Host-specific rendering functions stay explicit; the registry is not a plugin framework. Parser choices,
installation inspection, capability reporting, CLI ordering, and support-matrix tests read the registry.
Display labels and interactive/headless invocation examples remain ordinary documentation; they are not
registry fields until production code actually consumes them.

Registry-parameterized tests prove every declared target receives the same semantic protocol obligations,
full skills are materialized byte-identically, installation and capability observation agree on hook
ownership, and advisory targets never claim enforcement.

### 7. Final agent A/B contract alignment

Only after the production contracts, generated guidance, inspection, and registry pass their review gates
does the experiment align with the finished behavior. Treat the experiment as an external consumer of the
gate wire protocol, not as an internal user of `GateDecision`. The subprocess-only CLI harness declares
the single schema version it supports and validates the returned JSON envelope before the experiment uses
it. It deliberately does not import PEBRA internals: this preserves the process boundary and makes an
incompatible schema change fail loudly instead of silently changing experimental treatment.

The treatment arm continues to use `consult_only=True`. Because the assay has no trusted human approver,
an unresolved review outcome remains a conservative candidate hold. The experiment may retain
`schema_version`, tier, warning, risk summary, and host-only attribution internally, but the coding agent
sees exactly the normalized `{ok, blocked, reason}` fields in every arm. A held candidate produces no
write and no post-write assessment attribution. An allowed exact candidate is attributed only after a
successful write. For `ask_human`, the existing human-review arm continues to prove approval, exact
sanction, and fresh reassessment before a formerly held candidate may proceed. Persisted `reject` is not
sanctionable and must take a different route.

The reason now includes neutral candidate-bound assessment evidence when available. That is an intentional
treatment-content change even though the JSON field set is stable. The experiment's blinding validator
must permit only the approved neutral evidence vocabulary and must continue to reject product, oracle,
arm, experiment, or provider identifiers. The aligned experiment receives a fresh design hash and run ID;
pre-change checkpoints and outcomes are incompatible and cannot be resumed, pooled, or compared as one
treatment population.

The enforced positive control is not a production gate response. Its `positive_control` tier remains a
local experimental label, is documented as such in the runner, and must not be accepted by the production
permission/tier matrix. Deterministic experiment tests lock these boundaries. The live provider-backed
assay remains separately gated and is not run merely to implement this alignment.

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

Add typed candidate dispositions/wire permissions, tiers, schema version, allowed-pair validation,
exact-candidate risk summaries, host-specific permission projection, contract documentation, and the
single candidate-binding constant. Prove the versioned envelope and both Claude/Codex restrictive paths
through real subprocess E2E tests, but do not update or run the complete A/B suite yet. Stop for review
after focused tests, full tests, lint, import contracts, and the milestone's gate-contract E2E acceptance
pass.

### Milestone 2 — Always-loaded Claude rule and inspection

Add the managed Claude rule, semantic projection tests, and non-mutating `agent-init --check --json`.
Stop for review after proving check mode performs no writes for every state.

### Milestone 3 — Registry and cross-host conformance

Replace duplicated two-host facts with the minimal registry and parameterized conformance matrix. Do not
add a third runtime. Stop for review after focused host E2E and installed-wheel evidence.

### Milestone 4 — Experiment alignment and aggregate proof

Align the existing A/B runner and its production-shaped test doubles with the completed schema-1 behavior.
Preserve consult-only execution, the `{ok, blocked, reason}` field set, post-write-only attribution,
telemetry definitions, arm definitions, prompts, corpus, oracle, and scoring. Deliberately version the
new reason content as a new treatment: generate a fresh design hash and run ID and prohibit resume or
pooling with earlier results. Run the full deterministic A/B suite and aggregate `e2e-fast` only at this
final milestone, then require Windows/Ubuntu/macOS CI evidence. Do not launch the separately gated
provider-backed live assay without explicit authorization.

### Deferred runtime expansion

A later spec may add a generic advisory target or individually verified hosts such as OpenCode or Qwen.
Each target must first prove its real instruction-loading convention, Windows materialization, headless
invocation, and honest guarantee tier. Unverified hooks, model-provider targets, and presence-only support
claims remain forbidden.

## Verification requirements

- TDD regressions for malformed JSON and every invalid hook shape.
- A regression locks `HOOK_COMMAND == "pebra gate-hook"` as the current installed compatibility contract;
  changing it requires legacy-signature migration tests in the same change.
- Byte-for-byte no-write assertions on validation and check paths.
- Complete enum and allowed-pair coverage.
- Exact-candidate risk-summary validation rejects partial, non-finite, stale, mismatched, unbound, and
  unverifiable evidence; restrictive reasons never imply that the user's goal was rejected.
- Both installed hooks project `REQUEST_HUMAN` to a blocking candidate hold; Claude cannot bypass PEBRA's
  sanction/reassessment through native prompt approval, and Codex never receives unsupported `ask`.
  The Claude and Codex event paths are proved across their subprocess boundaries.
- Documentation rows derived from live contract values.
- A focused production subprocess E2E proves the schema-1 envelope before the Milestone 1 review.
- In the final milestone, the A/B subprocess harness rejects unsupported or malformed envelopes without
  importing PEBRA.
- The A/B treatment still forwards `consult_only=True`, preserves post-write-only assessment attribution,
  and exposes only `{ok, blocked, reason}` to the model when gate metadata is present.
- Held candidates produce neither a file write nor applied/proceeded assessment attribution; they may
  remain intervention-only observations. The `ask_human` path proves approval, exact sanction, and
  reassessment before a formerly held candidate proceeds, while `reject` requires a different route.
- The candidate-bound reason treatment uses a fresh design hash/run ID and rejects resume or pooling with
  pre-change experiment results.
- `positive_control` remains experiment-local and absent from the production `GateTier` contract.
- Candidate-binding consumers use the single core constant.
- Claude always-loaded rule and full skills contain the required semantic obligations.
- Full Claude and Codex skills are byte-identical.
- Registry, parser choices, capability order, inspection, and README support declarations cannot drift.
- Each milestone passes focused subprocess E2E acceptance for the behavior it introduces before review.
- `nox -s tests lint` passes at every milestone; the complete deterministic A/B suite and
  `nox -s e2e-fast` run in the final experiment milestone.
- Distribution verification proves all generated templates remain available from an installed wheel.
- Final Ubuntu, Windows, and macOS CI runs only after experiment alignment and is required before any
  Agent Integration V2 runtime-support claim or release. The bounded predecessor `0.1.1` release follows
  its separately approved release design and does not claim the post-release V2 contract, registry, or
  experiment alignment.

## Non-goals

- Changing `HOOK_COMMAND` or automatically migrating a legacy PEBRA hook signature; either requires a
  separate approved migration design with known legacy signatures and deduplication evidence.
- Changing decision math, the persisted six-value `Decision` enum, sanctions, or persistence. Gate-facing
  wording and host projection change only how the exact candidate disposition is communicated and enforced.
- Replacing the pre-act gate with CI-after-the-fact enforcement.
- A multi-agent inbox, work queue, or coordinator.
- A plugin engine, third-party risk-rule loader, or self-updater.
- Symlink or pointer-file projection.
- Markdown as the canonical assessment ledger.
- Adding Gemini, DeepSeek, OpenCode, Qwen, Kimi, Grok, or any other runtime in this implementation slice.
