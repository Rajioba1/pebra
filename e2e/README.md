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

## Current Scope

Current slice: `agent_cli_seeded_learning + dashboard_metrics`.

It is not full Tauri-level coverage yet. Remaining gated lanes:

- `E2E_CODEGRAPH=1`: real CodeGraph graph/fan-in product path.
- `E2E_ORGANIC=1`: organic 100+ edit learning lane with no seeded-history shortcut.
- `E2E_UI=1`: Playwright dashboard screenshot for human visual review.

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

## Machine Assertions

The suite asserts:

- CLI-only boundary discipline.
- PEBRA returns decision/math/guidance to the agent.
- Verify passes only after the edit is applied inside the approved envelope.
- Completed outcomes can be recorded and learned.
- Promotion writes at least one active risk snapshot after the seeded history.
- A future pre-edit assessment shifts decision/math after learning.
- Dashboard API exposes chain, overview, and assessment history for the learned run.
