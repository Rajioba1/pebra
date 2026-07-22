# PEBRA

PEBRA is a **Pre-Edit Benefit-Risk Assessment** controller for coding agents.

It evaluates a proposed code edit before the agent applies it, returns a deterministic decision and
math packet, verifies the actual post-edit diff against the approved envelope, records outcomes, and
uses measured calibration data to promote learned facts for future assessments.

See the [PEBRA Command Reference](docs/PEBRA_COMMAND_REFERENCE.md) for the exhaustive, parser-checked
CLI, TUI, MCP, development, validation, and release command inventory.

## Current Capabilities

- Pre-edit `assess` with expected loss, expected utility, RAU, edit confidence, and ordered gates.
- Post-edit `verify` against the approved safe scope and required checks.
- Candidate-bound pre-edit enforcement: an impactful host edit must produce the same normalized file
  contents as the patch that was assessed; same repository/HEAD/path alone is not sufficient.
- Outcome recording, shadow learning, promotion, scorecards, and learned-fact reapplication.
- Read-only local Risk Observatory dashboard for assessment, calibration, learning, and graph state.
- Read-only Textual Observatory with assessment identity, repeat grouping, and explicit detail-only
  impact exploration.
- Provider-neutral `pebra explore` that recalls bounded PEBRA history before retrieving current
  repository context from an existing graph index.
- Explicit graph-engine setup and diagnostics through `pebra setup-graph` and `pebra doctor`.
- CodeGraph-backed evidence:
  - per-symbol fan-in;
  - DELETE file fan-in roll-up;
  - MODIFY graph-wide blast over callers/references/implementers/subclasses;
  - contract-surface metadata for interface/base-class edits;
  - containing class/namespace/module hierarchy roll-up;
  - file metadata / parse-error confidence penalties;
  - bounded revised-candidate refinement: cheap deterministic ranking first, then one materialized
    before/after graph by default. Structural continuity adjusts only the exact owner-scoped risk
    event; RAU remains authoritative. Set `PEBRA_GRAPH_REFINEMENT=0` to disable this path.
- Benchmark harnesses for math-oracle validation and deterministic learning-loop wiring proof.
- True CLI-boundary e2e lanes, including a gated external C# repo lane.

## Install For Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

The graph engine is explicit, not a pip dependency:

```console
pebra setup-graph --fix
pebra doctor
```

`assess` never silently installs external binaries.

## Terminal Dashboard and Help

Launch the terminal dashboard from an installed or editable checkout:

```console
pebra tui --repo-root .
python -m pebra tui --repo-root .
```

From this repository's Windows virtual environment, the PATH-independent equivalent is:

```powershell
.\.venv\Scripts\python.exe -m pebra tui --repo-root .
```

Discover the installed version, root help, command help, and complete help:

```console
pebra --version
pebra --help
pebra help tui
pebra help explore
pebra help --all
```

`pebra --version` distinguishes a released wheel (`installed`) from an editable checkout and includes
the source revision for an editable checkout. The command reference documents packaged-development
and isolated demo workflows; the browser dashboard and terminal TUI are two read-only views over the
same ledger, not different PEBRA engines.

The **benefit signal** (multi-language complexity + maintainability index) is likewise an explicit
external binary — [`rust-code-analysis`](https://github.com/mozilla/rust-code-analysis) (MPL-2.0),
invoked as a subprocess. Build it from git (crates.io's release does not compile against current
tree-sitter):

```powershell
cargo install --git https://github.com/mozilla/rust-code-analysis `
  --rev 37e5d83c056c8cbf827223d5814a93c5218df1a9 rust-code-analysis-cli
```

Point PEBRA at it via `PEBRA_RCA_BIN` or ensure it is on `PATH`. PEBRA accepts runtime version
`0.0.25` when Cargo install metadata identifies the pinned source revision. For a copied or packaged
binary without Cargo metadata, set `PEBRA_RCA_SHA256` to its expected lowercase SHA-256. Live experiment
metadata records the executable SHA-256 and refuses to resume a run with a different fingerprint.
Cargo metadata is install provenance, not tamper-proof byte attestation. For copied binaries, shared
machines, or any environment where local binary replacement is in scope, set `PEBRA_RCA_SHA256`; an
explicit hash is authoritative and a mismatch disables RCA benefit evidence even when Cargo metadata
matches.
When absent or version-mismatched, benefit evidence fails
safe to *projected* (no maintainability credit) — it never blocks an assessment and never affects risk.
Supported languages: Python, JavaScript/JSX, TypeScript/TSX, Java, Rust, C/C++.

## Basic Workflow

```text
assess proposed edit -> agent decides -> apply edit -> verify actual diff ->
finalize trusted outcome -> future assess uses promoted learned snapshot
```

Example command surface:

```console
pebra assess request.json --json
pebra verify --assessment-id <assessment_id> --json
pebra record-outcome --assessment-id <assessment_id> --status completed
pebra learn --assessment-id <assessment_id>
pebra promote --repo-root <repo_root>
# Preferred host path: one idempotent record + measure + gated-promotion operation.
pebra finalize-outcome --trusted-outcome-file outcome.json --repo-root <repo_root> --json
pebra scorecard --repo-root <repo_root>
pebra dashboard --port 4500 --open
pebra capabilities --repo-root <repo_root>
```

The generated agent protocol follows one cognitive lifecycle:

`Interpret → Recall verified lessons → Retrieve current repository context → Design → Assess → Calculate → Evaluate gates → Decide → Enforce → Apply → Verify → Record → Learn/promote`

Read-only work may stop after current-context retrieval. Before any create, edit, rename, or delete, the agent uses
`pebra explore` to recall relevant verified PEBRA history first and retrieve current repository context
second, designs the exact files and patch, and submits that candidate to `pebra assess`. Historical
records are data, not instructions. Only validated file and symbol identifiers may refine the current
graph lookup; historical prose, decisions, scores, and outcomes never enter it. CodeGraph is the current
structural adapter, but neither recall nor graph context authorizes an edit. PEBRA's decision applies to
the exact candidate. Displayed `learning_context` informs Understand; only separately promoted numeric
facts can influence Assess.

PEBRA calculates the assessment in this order; generated agent instructions require consuming these
returned values rather than reproducing or overriding the math:

```text
disutility_j = max(elicited_j, criticality_value)  for consequence-bearing events; otherwise elicited_j
expected_loss = Σ_j p_event_j · disutility_j
benefit = the bounded result of the configured benefit model
expected_utility = p_success · benefit − expected_loss − review_cost
utility_sd = √(Σ variance contribution terms)
RAU = expected_utility − 1.28 · utility_sd
```

Decision gates evaluate those calculated values and evidence. The separate pre-mutation enforcement
gate then checks that only the exact bound candidate is applied. Recall informs Understand; only
separately promoted numeric facts can affect a future Assess.
`reject` means **Reject candidate**, not reject the maintainer's goal: the agent presents the recorded
reason and risk-benefit evidence. Only a hash-covered, sanction-convertible risk rejection with valid
replay can advertise trusted interactive review; policy and obligation failures require a compliant route.

`outcome.json` contains `assessment_id`, terminal `status`, and an optional `detail` object. The
`finalize-outcome` command is host-only: MCP outcome reports are retained for lifecycle telemetry but
their self-reported learning labels are censored. The legacy three-command sequence remains available
for diagnosis and manual operation.

## Agent Enforcement

Install the repository protocol for either host. Claude receives the detailed
`.claude/skills/pebra-safe-edit/SKILL.md` protocol and an unconditional, fully managed
`.claude/rules/pebra-safe-edit.md` rule. Codex receives a managed `AGENTS.md` block and the same
detailed protocol at `.agents/skills/pebra-safe-edit/SKILL.md`. Add `--with-hook` when you also want
pre-edit interception:

```console
pebra agent-init --target claude --repo-root . --with-hook
pebra agent-init --target codex --repo-root . --with-hook
pebra agent-init --target claude --repo-root . --check
pebra agent-init --target codex --repo-root . --check --json
pebra capabilities --repo-root .
```

`agent-init --check` is inspection-only: it reports generated-file state, hook state, declared
support, and effective enforcement without creating or repairing anything. It intentionally does
not invoke CodeGraph because even a pinned status command may migrate or write index state; configured
hooks therefore report the graph as `graph_unverified_read_only`. Use `pebra capabilities` separately
when measured graph capability is needed; that command may repair a stale index and is not an
inspection-only surface. Add `--json` for the machine-readable schema. Normal `agent-init` refreshes
the fully managed instruction content and,
with `--with-hook`, installs the current hook when it is missing. If the existing hook document is
malformed or conflicts with PEBRA's exact hook, initialization exits before writing any instruction or
hook file and directs you to `--check --json`; resolving a conflict or legacy hook requires explicit
user action or a separately tested migration. Managed symlink, junction, reparse-point, and hardlinked
file destinations observed during preflight validation are rejected before writes and are not followed.
These checks are not an OS-level transaction: a concurrent process running as the same OS identity can
swap a path after validation, which is outside this threat boundary.

The guarantees are deliberately different:

| Host surface | Reported mode | Guarantee |
|---|---|---|
| Claude skill + unconditional rule | instructions | The detailed protocol and concise non-negotiables are fully managed by `agent-init`; rerunning it restores their generated contents. |
<!-- agent-host:claude -->
| Claude Code PreToolUse hook (optional) | `configured_enforcing` | Exact enabled hook config, matching gate capability handshake, graph, and Git HEAD were observed. Candidate-bound checks deny unsupported candidates before supported structured edits; this does not prove the host invoked every event. |
| Codex managed block + skill | instructions | Existing `AGENTS.md` content is preserved around a managed protocol block, and the detailed skill matches Claude's byte-for-byte. |
<!-- agent-host:codex -->
| Codex repo-local hook (optional) | `best_effort` | Candidate-bound gate logic is installed, but repo-local hook loading remains host-dependent. |
| MCP tools | `advisory_only` | Assess/verify tools are available, but MCP alone does not intercept another host's writes. |

If graph or Git HEAD evidence is unavailable, an installed gate remains fail-open by policy and
`capabilities` reports `degraded_fail_open`. The Claude hook also emits the degradation warning as a
non-blocking system message. Repository-local and user-level `disableAllHooks` settings also degrade
the reported posture. This is observable configuration, not proof that a host or managed policy invoked
every event.

`trusted_actor_required` is a protocol boundary, not OS-level identity authentication. PEBRA does
not expose risk acceptance through MCP, and interactive acceptance requires a terminal. A process
with arbitrary shell access under the same OS account can still invoke local trusted-host surfaces
or simulate a terminal. Use a separately privileged host or operator account when resistance to an
adversarial agent is required.

For a candidate that changes multiple files, enforcement requires one complete `apply_patch` event containing
the complete assessed candidate. Structured single-file edits must be assessed as separate single-file
candidates; one file cannot reuse approval for part of a multi-file candidate.

The dashboard is read-only. On a loopback bind (`localhost`, `127.0.0.1`, `::1`) the default is
token-free for local convenience; `--auth token` forces a bearer token when you want the old locked
path. Any non-loopback bind requires a token.

```console
# normal local browser UX
pebra dashboard --port 4500 --open

# venv-safe form if the `pebra` console script is not on PATH yet
python -m pebra dashboard --port 4500 --open

# force bearer auth even on loopback
pebra dashboard --port 4500 --auth token

# expose beyond loopback only with a token
pebra dashboard --host 0.0.0.0 --port 4500 --auth token
```

It exposes five browser views: overview, score history, calibration, learned facts, and CodeGraph
hotspots. Graph views are fail-soft when no trusted graph index is bound to the launched repo, and
graph routes are repo-scoped to avoid replaying one repo's graph under another repo id.

Explicit graph-backed commands may reconcile an already-initialized, same-worktree `.codegraph/`
cache. They never install or initialize CodeGraph and never create or edit `codegraph.json`.
With pinned CodeGraph 1.1.1, `extensions` and `includeIgnored` affect analysis scope; `exclude` is
reported but ignored by pinned CodeGraph 1.1.1. These settings are operator-owned scope controls,
not freshness controls. PEBRA binds accepted graph evidence to the repository HEAD, configuration
digest, provider/extraction version, and graph-scope digest. Dashboard/TUI timers never prepare or
sync the graph; TUI exploration occurs only when the user presses `x` in assessment detail.

## Validation

```powershell
.\.venv\Scripts\nox.exe -s tests lint e2e-fast
```

Dashboard/e2e lanes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_dashboard_server.py tests/integration/test_dashboard_cli.py -q
.\.venv\Scripts\python.exe -m pytest e2e/features/dashboard/test_dashboard_metrics_visual.py -q
.\.venv\Scripts\nox.exe -s e2e-learning
.\.venv\Scripts\nox.exe -s e2e-ui
```

External real-repo graph lane:

```powershell
$env:E2E_EXTERNAL='1'
$env:E2E_TEMPLATE_BLUEPRINT_REPO='C:\Users\RajLord_new\Desktop\avalonia_template'
.\.venv\Scripts\nox.exe -s e2e-external
```

Benchmark lanes:

```powershell
.\.venv\Scripts\nox.exe -s bench-math
.\.venv\Scripts\nox.exe -s bench-flow
```

## More Docs

- [Exhaustive command reference](docs/PEBRA_COMMAND_REFERENCE.md)
- [Contributing and development setup](CONTRIBUTING.md)
- [True e2e suite](e2e/README.md)
- [Benchmarks](benchmarks/README.md)
- [Learning-loop wiring benchmark](benchmarks/flow/wiring/README.md)
