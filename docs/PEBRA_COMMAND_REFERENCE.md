# PEBRA Command Reference

This is the exhaustive operator and contributor command reference for the current source tree. The authoritative product parser is `pebra/cli/main.py`; verify the installed version at any time with:

```console
pebra help --all
pebra --version
```

The current tree has 22 root CLI commands, two installed console entrypoints, four MCP tools, 17 nox sessions, repository packaging utilities, and three GitHub Actions workflows.

## Shell Compatibility

PEBRA's product commands are terminal-agnostic. Once `pebra` is installed and on `PATH`, commands such as `pebra assess`, `pebra tui`, `pebra dashboard`, and `pebra help` use the same arguments in PowerShell, Command Prompt, Bash, zsh, and other ordinary terminals on the supported operating systems.

Code fences labelled `console` contain shell-neutral commands. A `powershell`, `cmd`, or `bash` label means that the example contains shell-specific activation, environment-variable, path, piping, globbing, or command-substitution syntax; it does **not** mean PEBRA itself is restricted to that shell.

| Operation | PowerShell | Command Prompt | Bash / zsh |
| --- | --- | --- | --- |
| Virtual-environment executable directory | `.venv\Scripts` | `.venv\Scripts` | `.venv/bin` |
| Activate the virtual environment | `.\.venv\Scripts\Activate.ps1` | `.venv\Scripts\activate.bat` | `source .venv/bin/activate` |
| Set one environment variable | `$env:NAME = "value"` | `set NAME=value` | `export NAME="value"` |
| Pipe a JSON file to stdin | `Get-Content -Raw event.json \| pebra gate-check` | `type event.json \| pebra gate-check` | `cat event.json \| pebra gate-check` |

Forward-slash paths used in shell-neutral examples are accepted by Python on Windows as well as on Linux and macOS. If a path contains spaces, quote it in every shell.

## Installation

### Install the released package

```console
python -m pip install --upgrade pip
python -m pip install pebra
pebra --version
pebra help
```

For an isolated command installation:

```console
pipx install pebra
pebra --version
```

### Editable source installation

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

Command Prompt:

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e .
```

Bash or zsh on Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Full UI and agent-experiment extras (shown for PowerShell; substitute the executable path from the table above in another shell):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ui-e2e,agent-ab]"
.\.venv\Scripts\python.exe -m pip install nox textual-dev
```

Core/import-boundary installation without runtime adapter dependencies (PowerShell):

```powershell
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

After activating the environment, the shorter `python`, `pebra`, `nox`, and `textual` forms are shell-neutral.

## Runtime and Development Modes

| Mode | What runs | Provenance / state |
| --- | --- | --- |
| Released | `pip install pebra`; then `pebra ...` | `pebra --version` reports `installed` |
| Editable source | `pip install -e .`; then `pebra ...` or `python -m pebra ...` | reports `editable` plus checkout revision |
| Packaged development | `python -m scripts.dev_package` | builds and tests a clean temporary wheel; does not publish |
| Isolated demo | `python -m scripts.demo_observatory` | temporary synthetic DB, `repo_demo_*`, visible `DEMO`; not a root command |
| Browser Observatory | `pebra dashboard` | read-only web view of the selected ledger |
| Terminal Observatory | `pebra tui` | read-only Textual view of the same ledger |

## Installed Entrypoints

```text
pebra       Main CLI
pebra-mcp   MCP stdio server
```

Equivalent Python module forms:

```console
python -m pebra
python -m pebra.mcp_server
```

## Root Help and Version

```console
pebra -h
pebra --help
pebra --version
pebra -V
pebra help
pebra help COMMAND
pebra help --all
pebra help help
```

Every subcommand also accepts `-h` or `--help`.

## Product CLI

### `assess`

Assess one candidate edit from a JSON request file.

```text
pebra assess REQUEST_FILE
  [--json]
  [--repo-root PATH]
  [--db PATH]
  [--include-host-metadata]
  [--trusted-candidate-verification-file PATH]
  [--trusted-task-obligations-file PATH]
```

Typical use:

```console
pebra assess examples/login_patch.json
pebra assess request.json --repo-root . --json
```

The trusted files and host-metadata option are host integration inputs, not model-controlled request evidence.

### `accept-risk`

Create a sanction from JSON, or interactively approve, reassess, and apply an exact pending candidate.

```text
pebra accept-risk [SANCTION_FILE]
  [--apply]
  [--assessment-id ID]
  [--repo-root PATH]
  [--db PATH]
```

```console
pebra accept-risk sanction.json --repo-root .
pebra accept-risk --apply --assessment-id asm_12 --repo-root .
```

The interactive `--apply` route requires a real TTY and human approval.

### `apply-candidate`

Apply the exact cached candidate for an authorized assessment.

```text
pebra apply-candidate --assessment-id ID
  [--repo-root PATH]
  [--db PATH]
```

```console
pebra apply-candidate --assessment-id asm_12 --repo-root .
```

### `agent-init`

Install or inspect PEBRA instructions and optional host hooks.

```text
pebra agent-init --target {claude,codex}
  [--repo-root PATH]
  [--with-hook]
  [--check]
  [--json]
```

```console
pebra agent-init --target claude --repo-root .
pebra agent-init --target claude --repo-root . --with-hook
pebra agent-init --target codex --repo-root . --check
pebra agent-init --target codex --repo-root . --check --json
```

`--json` requires `--check`. Inspection mode is non-mutating and intentionally does not invoke CodeGraph.

### `verify`

Compare the actual diff with a stored assessment's approved envelope.

```text
pebra verify --assessment-id ID
  [--scope {staged,all,branch}]
  [--completed-check CHECK=STATUS]...
  [--dry-run-preview]
  [--repo-root PATH]
  [--db PATH]
  [--json]
```

```console
pebra verify --assessment-id asm_12 --scope staged --json
pebra verify --assessment-id asm_12 --completed-check "pytest -q=passed" --json
```

`branch` currently behaves as working-tree-versus-HEAD, not true branch-base comparison.

### `record-outcome`

Record a terminal action outcome.

```text
pebra record-outcome --assessment-id ID
  --status {completed,skipped,rejected}
  [--detail JSON]
  [--repo-root PATH]
  [--db PATH]
```

```console
pebra record-outcome --assessment-id asm_12 --status completed
pebra record-outcome --assessment-id asm_12 --status completed --detail '{"actual_success":true}'
```

Recognized detail labels include `actual_success`, `event_outcomes`, `benefit_realized`, `actual_review_cost`, and `actual_rework_cost`. Missing labels remain censored.

### `finalize-outcome`

Preferred trusted-host lifecycle path: record outcome, measure, and run gated promotion.

```text
pebra finalize-outcome --trusted-outcome-file PATH
  [--repo-root PATH]
  [--db PATH]
  [--json]
```

```console
pebra finalize-outcome --trusted-outcome-file outcome.json --repo-root . --json
```

### `learn`

Record shadow learning measurement without changing decisions.

```text
pebra learn --assessment-id ID
  [--repo-root PATH]
  [--db PATH]
  [--json]
```

```console
pebra learn --assessment-id asm_12 --repo-root . --json
```

### `promote`

Run shadow-to-active learned-fact promotion.

```text
pebra promote
  [--repo-root PATH]
  [--db PATH]
  [--drift-freeze-threshold FLOAT]
  [--json]
```

```console
pebra promote --repo-root . --json
```

### `scorecard`

Read calibration and benefit metrics.

```text
pebra scorecard
  [--repo-root PATH]
  [--db PATH]
  [--json]
```

```console
pebra scorecard --repo-root . --json
```

### `dashboard`

Launch the browser-based read-only Risk Observatory.

```text
pebra dashboard
  [--repo-root PATH]
  [--db PATH]
  [--repo-id ID]
  [--read-only]
  [--host HOST]
  [--port PORT]
  [--instance N]
  [--auth {auto,token,none}]
  [--token]
  [--open]
```

```console
pebra dashboard --port 4500 --open
pebra dashboard --host 127.0.0.1 --port 0
pebra dashboard --auth token --open
pebra dashboard --host 0.0.0.0 --auth token
pebra dashboard --read-only --db path/to/pebra.db --repo-id repo_example
```

`--token` is an alias for forcing `--auth token`; it does not accept a token value. Non-loopback binds require token authentication.

### `tui`

Launch the Textual read-only Observatory.

```text
pebra tui
  [--repo-root PATH]
  [--db PATH]
  [--repo-id ID]
  [--read-only]
```

```console
pebra tui --repo-root .
python -m pebra tui --repo-root .
pebra tui --read-only --db path/to/pebra.db --repo-id repo_example
```

`--repo-root` requires a value. Use `--repo-root .` for the current directory.

### `setup-graph`

Install or initialize the graph engine for a repository/worktree.

```text
pebra setup-graph
  [--repo-root PATH]
  [--fix]
  [--version VERSION]
  [--allow-unsupported]
  [--via {auto,standalone,npm}]
  [--json]
```

```console
pebra setup-graph --fix --repo-root .
pebra setup-graph --via npm --repo-root .
pebra setup-graph --version 1.1.1 --repo-root .
```

`PEBRA_CODEGRAPH_BIN` overrides graph-engine discovery.

Initialization preserves an existing `codegraph.json` byte-for-byte, then runs the same fenced graph
preparation used by assessment and exploration. It does not scaffold graph configuration.

### `doctor`

Diagnose graph availability; add `--fix-graph` for an explicitly mutating repair.

```text
pebra doctor [--repo-root PATH] [--fix-graph] [--json]
```

```console
pebra doctor --repo-root .
pebra doctor --repo-root . --fix-graph --json
```

Doctor reports whether `codegraph.json` exists, its raw-byte SHA-256 digest (or `absent`), the
structurally valid `extensions` and `includeIgnored` values supported by managed CodeGraph 1.1.1, and
any `exclude` key as unsupported. `extensions` and `includeIgnored` affect analysis scope; `exclude`
is reported but ignored by pinned CodeGraph 1.1.1. Malformed configuration is reported and never repaired. Without
`--fix-graph`, doctor is read-only.

### `graph-stats`

Report CodeGraph node counts.

```text
pebra graph-stats [--repo-root PATH] [--json]
```

```console
pebra graph-stats --repo-root . --json
```

### `capabilities`

Report measured language support and observed host-enforcement posture.

```text
pebra capabilities [--repo-root PATH] [--json]
```

```console
pebra capabilities --repo-root . --json
```

Unlike `agent-init --check`, this command may repair a stale graph index.

### `candidate-patch`

Build a deterministic unified diff from structured replacements.

```text
pebra candidate-patch EDITS_FILE [--repo-root PATH] [--json]
```

```console
pebra candidate-patch edits.json --repo-root . --json
```

### `gate-check`

Read one host edit event from stdin and print a versioned gate decision.

```text
pebra gate-check
  [--db PATH]
  [--consult-only]
  [--include-host-metadata]
```

```powershell
Get-Content -Raw event.json | pebra gate-check
Get-Content -Raw event.json | pebra gate-check --consult-only
```

```cmd
type event.json | pebra gate-check
```

```bash
cat event.json | pebra gate-check
```

`--consult-only` is for hosts/runners without a trusted human approver. Host metadata includes the matched assessment ID and is not model-facing treatment content.

### `gate-hook`

Claude PreToolUse compatibility/enforcement shim.

```text
pebra gate-hook [--db PATH]
```

Internal handshake, deliberately hidden from normal help:

```text
pebra gate-hook --capabilities
```

Do not treat `--capabilities` output as candidate authorization.

### `dependents`

List files that depend on one target file.

```text
pebra dependents --target PATH [--repo-root PATH] [--json]
```

```console
pebra dependents --target pebra/observatory_context.py --repo-root .
pebra dependents --target pebra/observatory_context.py --repo-root . --json
```

### `explore`

Return bounded, descriptive repository context from an existing same-worktree graph index.

```text
pebra explore [QUERY] [--file PATH]... [--max-files N] [--max-bytes N]
              [--repo-root PATH] [--json]
```

`QUERY` is required unless at least one `--file` is supplied. `--max-files` is clamped to `1..32`
and `--max-bytes` to `1000..100000`. The default bounds are 8 files and 24000 UTF-8 bytes.

```console
pebra explore "repository resolution" --repo-root .
pebra explore --file pebra/observatory_context.py --repo-root . --json
```

The command explicitly reconciles an already-initialized index before querying it, then revalidates
the repository HEAD, raw `codegraph.json` digest, provider version, extraction version, and graph scope
after the query. It never installs an engine, initializes an index, repairs a worktree mismatch, or
creates/edits `codegraph.json`. Free-text context is opaque descriptive output and never becomes
trusted assessment evidence. Affected tests come only from the structurally validated provider JSON;
dependent files use the existing prepared dependency-reader path.

An unavailable or stale provider is a handled result: the command returns exit 0 with
`status != "available"` and empty context/file/test fields. Invalid arguments return argparse exit 2.
Unexpected adapter contract failures return exit 1. Provider queries may perform transient SQLite
WAL/SHM housekeeping inside the derived cache; source, configuration, `.pebra`, Git content,
persistent database bytes, graph schema, and logical graph rows must remain unchanged.

`codegraph.json` is operator-owned analysis scope. `extensions` and `includeIgnored` affect analysis
scope; `exclude` is reported but ignored by pinned CodeGraph 1.1.1. None of these settings makes an
index fresh. PEBRA never guesses, scaffolds, or edits graph configuration. Results from different
scope digests are not interchangeable for learning or experiments.

### `help`

```text
pebra help [COMMAND] [--all]
```

`pebra help --all` prints all visible command syntax. `pebra help help` prints the help command's own syntax.

## Standard Product Workflows

### Pre-edit lifecycle

The agent lifecycle is:

`Interpret → Understand → Design → Assess → PEBRA decides → Apply → Verify`

Read-only work may stop after Understand. Mutation does not: first reuse equivalent current repository
context or run `pebra explore`, then design the exact `expected_files` and `proposed_patch`, and run
`pebra assess` before writing. `pebra explore` uses PEBRA's provider-neutral repository-graph interface;
CodeGraph is the current adapter. Its bounded context, dependents, and affected tests are descriptive and
cannot authorize a write. PEBRA decides for the exact candidate. A `reject` is shown as **Reject
candidate** with its recorded reason and metrics; only an eligible trusted-human route may override a
sanction-convertible risk rejection, while policy or obligation failures require a compliant route.

```console
pebra assess request.json --json
pebra apply-candidate --assessment-id asm_12
pebra verify --assessment-id asm_12 --completed-check "pytest -q=passed" --json
pebra record-outcome --assessment-id asm_12 --status completed
pebra learn --assessment-id asm_12
pebra promote --repo-root .
pebra scorecard --repo-root .
```

Trusted hosts should prefer `finalize-outcome` over manually chaining `record-outcome`, `learn`, and `promote`.

### Agent integration inspection

```console
pebra agent-init --target claude --repo-root . --with-hook
pebra agent-init --target codex --repo-root . --with-hook
pebra agent-init --target claude --repo-root . --check --json
pebra agent-init --target codex --repo-root . --check --json
pebra capabilities --repo-root . --json
```

## TUI Keys and Commands

Product-defined keys:

| Key | Action |
| --- | --- |
| `q` | Quit |
| `?` | Toggle key-help panel |
| `Escape` | Close help panel or return from assessment detail |
| `r` | Refresh the Observatory ledger |
| `g` | Toggle contiguous exact-candidate grouping (`Group repeats` / `Show raw`) |
| `x` | Explore impact from assessment detail only |

The detail-only `x` action requires repository context and an injected explorer; a read-only replay
launched without `--repo-root` reports exploration as unavailable and does not construct a provider.
When available, `x` visibly prepares/reconciles the existing derived graph cache and then queries that
accepted snapshot. It is single-flight: another `x` press is ignored while work is in progress. It
never runs automatically on TUI launch, detail mount, row selection, five-second ledger refresh, or
background polling. Its bounded output is descriptive context only and never enters stored assessments,
scores, gates, sanctions, outcomes, or learning.

Inherited Textual bindings (dependency behavior, not PEBRA API):

| Key | Action |
| --- | --- |
| `Ctrl+Q` | Priority quit |
| `Enter` | Open the selected DataTable row |
| `Ctrl+P` | Open command palette |

Ledger/DataTable navigation under the current Textual 8.2.x environment:

| Key | Action |
| --- | --- |
| Arrow keys | Move cursor or pan |
| `Home` / `End` | Horizontal endpoints |
| `PageUp` / `PageDown` | Vertical paging |
| `Ctrl+PageUp` / `Ctrl+PageDown` | Horizontal paging |
| `Ctrl+Home` / `Ctrl+End` | First/last row |
| `Tab` / `Shift+Tab` | Move focus |
| `Ctrl+C` / `Super+C` | Copy selected text |

PEBRA command-palette commands:

```text
Refresh
Overview
Group repeats / Show raw
Help
```

Raw assessment history is the default. Grouping collapses only adjacent rows with the same validated
candidate fingerprint and identical displayed assessment semantics. A grouped `×N` row opens its latest
assessment and lists every contained assessment ID in detail. The caption distinguishes groups from raw
assessment count; overview counts and trends always remain based on raw assessments. Toggling back to
raw restores the previously selected exact assessment when it is still present.

Current Textual built-ins include:

```text
Theme
Keys
Maximize
Screenshot
Quit
```

Inherited bindings and built-ins may change within the allowed `textual>=8.2,<9` range. Product-defined bindings are the stable PEBRA contract.

## Isolated Observatory Demo

This source-only developer utility is intentionally absent from `pebra help`:

```console
python -m scripts.demo_observatory
python -m scripts.demo_observatory --dashboard
python -m scripts.demo_observatory --tui --keep
```

It creates a dedicated temporary SQLite database containing varied purpose-built rows, binds it to a
synthetic `repo_demo_*` identity, sets a visible `DEMO` label, and launches the existing surface with
`--read-only`. It never opens or copies the current checkout ledger. Without `--keep` the temporary
directory is removed after exit; with `--keep` its retained path is printed.

## Textual Development Console

Run from the repository root in two terminals. The development console is wired through `textual-dev`; it is not a separate PEBRA build.

Windows PowerShell:

```powershell
# Terminal 1
.\.venv\Scripts\textual.exe console
```

```powershell
# Terminal 2
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
.\.venv\Scripts\textual.exe run --dev -c "pebra tui --repo-root ."
```

Command Prompt:

```cmd
rem Terminal 1
.venv\Scripts\textual.exe console
```

```cmd
rem Terminal 2
set "PATH=%CD%\.venv\Scripts;%PATH%"
.venv\Scripts\textual.exe run --dev -c "pebra tui --repo-root ."
```

Bash or zsh on Linux/macOS:

```bash
# Terminal 1
.venv/bin/textual console
```

```bash
# Terminal 2
PATH="$PWD/.venv/bin:$PATH" .venv/bin/textual run --dev -c "pebra tui --repo-root ."
```

Regenerate visual baselines only after an intentional UI change:

```console
python -m pytest tests/snapshots --snapshot-update
python -m pytest tests/snapshots -q
```

## MCP Server and Tools

Start the stdio server with either entrypoint:

```console
pebra-mcp
python -m pebra.mcp_server
```

MCP tools:

### `pebra_assess`

Required:

```text
task: string
action: object
```

Optional: `evidence`, `thresholds`, `repo_root`, `db`.

### `pebra_compare`

Required:

```text
task: string
candidate_actions: array<object>
```

Optional: `evidence`, `thresholds`, `repo_root`, `db`.

There is currently no root `pebra compare` CLI command.

### `pebra_verify`

Required: `assessment_id`.

Optional: `scope`, `completed_checks`, `dry_run_preview`, `repo_root`, `db`.

### `pebra_record_outcome`

Required: `assessment_id`, `status`.

Optional: `detail`, `repo_root`, `db`.

MCP intentionally does not expose risk acceptance or candidate application.

## Local Validation

Direct tools:

```console
python -m pytest -q
python -m ruff check .
lint-imports
python -m pytest e2e/test_boundary_discipline.py -q
```

Normal source checkout gate:

```console
python -m nox -s tests lint e2e-fast
```

## Nox Sessions

List sessions:

```console
python -m nox --list
```

All 17 sessions:

| Command | Purpose / gate |
| --- | --- |
| `nox -s dev-package` | Build, install, and smoke-test the exact local wheel and sdist |
| `nox -s dev-package -- --open` | Same, then keep the installed dashboard open |
| `nox -s tests` | Full default test suite |
| `nox -s lint` | Ruff and import-linter architecture contracts |
| `nox -s bench-math` | Deterministic math/oracle benchmark |
| `nox -s bench-math-regen` | Regenerate frozen math fixtures |
| `nox -s bench-flow` | Deterministic learning-loop replay benchmark |
| `nox -s bench-flow-regen` | Regenerate flow corpus and frozen artifacts |
| `nox -s bench-continuity-smoke` | Unpaid multi-owner continuity evidence capture; requires `E2E_ZOD_REPO` |
| `nox -s bench-continuity-warm` | Pure-core cold/shipped/local prior check |
| `nox -s e2e` | Full deterministic product E2E including seeded learning |
| `nox -s e2e-fast` | Fast deterministic CLI/product boundary lane |
| `nox -s e2e-learning` | Seeded 100+ cycle learning/promotion/dashboard lane |
| `nox -s e2e-external` | Gated real external repository + CodeGraph proof; requires `E2E_EXTERNAL=1` |
| `nox -s e2e-ab` | Paid/provider-backed blinded agent assay; never run without explicit authorization |
| `nox -s e2e-ui` | Playwright browser-dashboard lane |
| `nox -s mcp-smoke` | Real MCP SDK and stdio server smoke |
| `nox -s core-only` | Base package/core import check without adapters |

External graph lane example:

```powershell
$env:E2E_EXTERNAL = "1"
$env:E2E_TEMPLATE_BLUEPRINT_REPO = "C:\path\to\external-repo"
.\.venv\Scripts\nox.exe -s e2e-external
```

```cmd
set E2E_EXTERNAL=1
set E2E_TEMPLATE_BLUEPRINT_REPO=C:\path\to\external-repo
.venv\Scripts\nox.exe -s e2e-external
```

```bash
export E2E_EXTERNAL=1
export E2E_TEMPLATE_BLUEPRINT_REPO=/path/to/external-repo
.venv/bin/nox -s e2e-external
```

## Benchmark Modules

Math benchmark:

```console
python -m benchmarks.math.export_fixture
python -m benchmarks.math.reference_metrics
python -m benchmarks.math.pebra_metrics
python -m benchmarks.math.compare
python -m benchmarks.math.run
python -m benchmarks.math.run --write
```

Learning-flow benchmark:

```console
python -m pytest benchmarks/flow -q
python -m benchmarks.flow.corpus.export_fixture
python -m benchmarks.flow.replay
python -m benchmarks.flow.compare
```

Continuity benchmark:

```text
python -m benchmarks.continuity.smoke --repo PATH [--output PATH]
python -m benchmarks.continuity.fit --input PATH --output PATH
  [--minimum-owner-clusters N] [--verify-frozen]
python -m benchmarks.continuity.warm [--output PATH]
```

There is no `pebra benchmark` command.

## Agent A/B Development Utilities

These are developer modules, not shipped product commands.

```text
python -m e2e.experiments.agent_ab.runners.orchestrator
  --run-id ID
  [--mode {smoke,pilot,powered,assay,assay_js}]
  [--preflight-only]
  [--skip-oracle-preflight]
  [--skip-graph-preflight]

python -m e2e.experiments.agent_ab.runners.watch_dashboard
  [--run-id ID]
  [--mode MODE]
  [--host HOST]
  [--port PORT]
  [--open]
  [--once]

python -m e2e.experiments.agent_ab.runners.launch_dashboard
  --run-id ID
  [--port PORT]
  [--index N]
```

Deterministic tests only:

```console
python -m pytest e2e/experiments/agent_ab/tests -q
```

`nox -s e2e-ab` invokes real providers and may incur cost. A passing deterministic suite is not authorization to run it.

## Packaging and Distribution Utilities

Build:

```powershell
.\.venv\Scripts\python.exe -m pip install build twine
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\twine.exe check dist\*
```

Install the single built wheel without hard-coding a version:

```powershell
$wheels = @(Get-ChildItem dist\pebra-*.whl)
if ($wheels.Count -ne 1) { throw "Expected exactly one PEBRA wheel; found $($wheels.Count)." }
$wheel = $wheels[0]
python -m venv .venv-package
.\.venv-package\Scripts\python.exe -m pip install $wheel.FullName
.\.venv-package\Scripts\pebra.exe --version
.\.venv-package\Scripts\pebra.exe help
```

Shell-neutral alternative after creating and activating a clean environment:

```console
python -m pip install --no-index --find-links dist pebra
pebra --version
pebra help
```

Local package exercise:

```console
python -m scripts.dev_package
python -m scripts.dev_package --open
```

Distribution verifier:

```text
python scripts/verify_distribution.py archives DIST_DIR
python scripts/verify_distribution.py installed
python scripts/verify_distribution.py codegraph
python scripts/verify_distribution.py checksums DIST_DIR
python scripts/verify_distribution.py verify-checksums DIST_DIR MANIFEST
python scripts/verify_distribution.py candidate-manifest DIST_DIR OUTPUT --tag TAG --commit SHA
python scripts/verify_distribution.py verify-candidate DIST_DIR MANIFEST --tag TAG --commit SHA
python scripts/verify_distribution.py index-digests DIST_DIR INDEX_JSON
python scripts/verify_distribution.py release-tag TAG [--pyproject PATH]
```

Regenerate the release dependency lock:

```console
pip-compile --allow-unsafe --generate-hashes --output-file=requirements-release.txt --strip-extras requirements-release.in
```

## GitHub Actions Jobs

### CI workflow

Runs on pushes to `main`, pull requests, or manual dispatch:

```text
Tests (ubuntu-latest)
Tests (windows-latest)
Tests (macos-latest)
Lint and architecture contracts
Build distributions
Installed wheel and CodeGraph (ubuntu-latest)
Installed wheel and CodeGraph (windows-latest)
Installed wheel and CodeGraph (macos-latest)
Playwright dashboard
```

Manual dispatch:

```console
gh workflow run ci.yml --ref main
```

### Secret scan workflow

```text
Gitleaks event/full-history scan
```

Manual dispatch:

```console
gh workflow run security.yml --ref main
```

### Release workflow

Inputs:

```text
release_tag       Required annotated tag on main
candidate_run_id  Optional prior release run to recover already-tested artifacts
```

Jobs:

```text
Build release candidate
Publish candidate to TestPyPI
Verify and publish tested bytes to PyPI
Create GitHub release
```

Dispatch—only after explicit maintainer release authorization:

```console
gh workflow run release.yml --ref main -f release_tag=vX.Y.Z
```

Recovery of an already-tested candidate—also requires explicit authorization:

```console
gh workflow run release.yml --ref main -f release_tag=vX.Y.Z -f candidate_run_id=RUN_ID
```

Passing tests, an approved milestone, a clean release candidate, or an existing tag does not itself authorize workflow dispatch, environment approval, PyPI publication, GitHub release creation, or rerunning a failed release.

## Release Verification Commands

Create an annotated tag only after the complete required matrix passes and the maintainer explicitly authorizes release:

```console
git tag -a vX.Y.Z -m "PEBRA X.Y.Z"
git push origin vX.Y.Z
```

Inspect workflows:

```console
gh run list --workflow ci.yml
gh run list --workflow security.yml
gh run list --workflow release.yml
gh run view RUN_ID --json status,conclusion,jobs,url
```

Download and verify a GitHub release:

```console
gh release download vX.Y.Z --dir release-download
python scripts/verify_distribution.py verify-checksums release-download release-download/SHA256SUMS
```

Clean PyPI install smoke:

Windows PowerShell:

```powershell
python -m venv .venv-release-smoke
.\.venv-release-smoke\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ pebra==X.Y.Z
.\.venv-release-smoke\Scripts\pebra.exe --version
.\.venv-release-smoke\Scripts\pebra.exe help
```

Command Prompt:

```cmd
python -m venv .venv-release-smoke
.venv-release-smoke\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ pebra==X.Y.Z
.venv-release-smoke\Scripts\pebra.exe --version
.venv-release-smoke\Scripts\pebra.exe help
```

Bash or zsh on Linux/macOS:

```bash
python3 -m venv .venv-release-smoke
.venv-release-smoke/bin/python -m pip install --index-url https://pypi.org/simple/ pebra==X.Y.Z
.venv-release-smoke/bin/pebra --version
.venv-release-smoke/bin/pebra help
```

## Environment Variables Used by Maintained Lanes

| Variable | Purpose |
| --- | --- |
| `PEBRA_CODEGRAPH_BIN` | Override CodeGraph launcher/bin discovery |
| `E2E_EXTERNAL=1` | Enable the gated external-repository E2E lane |
| `E2E_TEMPLATE_BLUEPRINT_REPO` | External repository used by the graph E2E lane |
| `E2E_UI_INSTALL_DEPS=1` | Allow browser lane to install Playwright system dependencies |
| `E2E_ZOD_REPO` | Repository used by continuity smoke capture |
| `TEXTUAL_ANIMATIONS=none` | Disable Textual animations/reveal effects |

The paid experiment has additional provider credentials and run controls documented in `e2e/experiments/agent_ab/README.md`; do not copy secrets into commands, logs, or documentation.

## Current Intentional Asymmetries

- `pebra_compare` exists in MCP; there is no `pebra compare` CLI command.
- Risk acceptance and candidate application exist only as trusted CLI/human workflows, not MCP tools.
- `gate-hook --capabilities` is internal and hidden from normal CLI help.
- `python -m scripts.demo_observatory` is a source developer utility, not a root `pebra` command.
- `pebra help --all` omits the help command's own detailed section; use `pebra help help`.
- Textual built-in commands and inherited key bindings are dependency behavior, not a stable PEBRA product contract.
- Benchmarks and agent-assay runners are developer modules/nox sessions, not root `pebra` commands.
