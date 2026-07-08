# PEBRA

PEBRA is a **Pre-Edit Benefit-Risk Assessment** controller for coding agents.

It evaluates a proposed code edit before the agent applies it, returns a deterministic decision and
math packet, verifies the actual post-edit diff against the approved envelope, records outcomes, and
uses measured calibration data to promote learned facts for future assessments.

## Current Capabilities

- Pre-edit `assess` with expected loss, expected utility, RAU, edit confidence, and ordered gates.
- Post-edit `verify` against the approved safe scope and required checks.
- Outcome recording, shadow learning, promotion, scorecards, and learned-fact reapplication.
- Read-only local dashboard for assessment/learning state.
- Explicit graph-engine setup and diagnostics through `pebra setup-graph` and `pebra doctor`.
- CodeGraph-backed evidence:
  - per-symbol fan-in;
  - DELETE file fan-in roll-up;
  - MODIFY graph-wide blast over callers/references/implementers/subclasses;
  - contract-surface metadata for interface/base-class edits;
  - containing class/namespace/module hierarchy roll-up;
  - file metadata / parse-error confidence penalties.
- Benchmark harnesses for math-oracle validation and deterministic learning-loop wiring proof.
- True CLI-boundary e2e lanes, including a gated external C# repo lane.

## Install For Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

The graph engine is explicit, not a pip dependency:

```powershell
pebra setup-graph --fix
pebra doctor
```

`assess` never silently installs external binaries.

## Basic Workflow

```text
assess proposed edit -> agent decides -> apply edit -> verify actual diff ->
record-outcome -> learn -> promote -> future assess uses learned snapshot
```

Example command surface:

```powershell
pebra assess request.json --json
pebra verify --assessment-id <assessment_id> --json
pebra record-outcome --assessment-id <assessment_id> --status completed --detail '{"actual_success": true}'
pebra learn --assessment-id <assessment_id>
pebra promote --repo-root <repo_root>
pebra scorecard --repo-root <repo_root>
pebra dashboard --port 0
```

## Validation

```powershell
.\.venv\Scripts\nox.exe -s tests lint e2e-fast
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

- [Development runbook](DEVELOPMENT.md)
- [True e2e suite](e2e/README.md)
- [Benchmarks](benchmarks/README.md)
- [Learning-loop wiring benchmark](benchmarks/flow/wiring/README.md)
