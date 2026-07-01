# PEBRA true e2e

This suite mirrors the Tauri-style product validation lane: real workflows over real process
boundaries, with deterministic JSON assertions plus optional screenshots for human review.

## What Is Real Here

- The scripted agent reaches PEBRA through `python -m pebra ...` subprocesses only.
- `e2e/test_boundary_discipline.py` fails if any `e2e/**/*.py` imports `pebra`.
- The learning lane runs the pre-edit workflow:
  `assess proposed edit -> apply edit -> verify actual diff -> record-outcome -> learn`.
- Promotion runs through `pebra promote`.
- The future assessment is a fresh pre-edit assessment on a clean tree with the learned snapshot active.
- The dashboard launches through `pebra dashboard --port 0` and is queried over HTTP.
- The external lane clones a real local repo into `e2e/out/`, initializes CodeGraph there, and validates
  graph-backed assessment over real CodeGraph + dotnet surfaces.

## Current Scope

Current slices:

- `agent_cli_seeded_learning + dashboard_metrics`;
- external CodeGraph graph-vs-no-graph DELETE delta;
- external CodeGraph graph-vs-no-graph MODIFY delta;
- real compiler-outcome learning on `avalonia_template`;
- outcome-to-graph diagnostic attribution as provenance.

It is not full Tauri-level coverage yet. Remaining gated lanes:

- `E2E_ORGANIC=1`: organic 100+ edit learning lane with no seeded-history shortcut.
- `E2E_UI=1`: Playwright dashboard screenshot for human visual review.
- agent A/B efficacy: two agents/worktrees, one guided by PEBRA and one unguided.

## Commands

Fast boundary/smoke lane:

```powershell
nox -s e2e-fast
```

Seeded learning/dashboard lane. This runs more than 100 CLI cycles and takes minutes on Windows:

```powershell
nox -s e2e-learning
```

Full current e2e lane:

```powershell
nox -s e2e
```

External real-repo graph lane. This is gated because it needs a local repo, CodeGraph, and dotnet:

```powershell
$env:E2E_EXTERNAL='1'
$env:E2E_TEMPLATE_BLUEPRINT_REPO='C:\Users\RajLord_new\Desktop\avalonia_template'
nox -s e2e-external
```

Dashboard screenshot lane:

```powershell
nox -s e2e-ui
```

## Artifacts

Generated local artifacts go under `e2e/out/` and are ignored by git:

- `e2e/out/screenshots/`
- `e2e/out/reports/`
- `e2e/out/dbs/`

Screenshots are not pixel-diffed. They are human-review artifacts.

## Human Labels

Reports and screenshots must use human labels, not storage-table names:

- `assessments` -> `Assessments run`
- `outcomes` -> `Completed outcomes`
- `prediction_errors` -> `Predictions checked`
- `risk_snapshots` -> `Learning snapshots`
- `learned_risk_facts` -> `Learned rules`

Graph-backed destructive-op reports should spell out the evidence in plain English:

- `Graph engine`
- `Graph freshness`
- `Changed operation`
- `File fan-in rollup`
- `Graph callers/references`
- `Risk event added`
- `Graph risk boost`
- `Final dependency-break probability`

Graph-backed MODIFY reports should spell out:

- `Graph engine`
- `Graph freshness`
- `Changed operation`
- `Symbol fan-in`
- `Modify impact`
- `Impacted edge types`
- `Contract surface`
- `Container hierarchy`
- `Indexed file metadata`
- `Risk event added`
- `Final dependency-break probability`

Learning reports should spell out:

- `Prior success estimate`
- `Learned success estimate`
- `Decision before learning`
- `Decision after learning`
- `Promotion evidence`
- `Real build outcomes`
- `Seeded outcomes`

## Machine Assertions

The suite asserts:

- CLI-only boundary discipline.
- PEBRA returns decision/math/guidance to the agent.
- Verify passes only after the edit is applied inside the approved envelope.
- Completed outcomes can be recorded and learned.
- Promotion writes at least one active risk snapshot after the seeded history.
- A future pre-edit assessment shifts decision/math after learning.
- Dashboard API exposes chain, overview, and assessment history for the learned run.
- External graph lane proves CodeGraph changed the DELETE and MODIFY assessment by comparing indexed
  and no-graph copies of the same repo/request.
- External compiler lane records a real dotnet build failure, then uses honest seeded history to prove
  promotion and future reassessment consume the learned snapshot.
