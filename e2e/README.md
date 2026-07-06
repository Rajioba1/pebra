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

Current deterministic product slice: `agent_cli_seeded_learning + dashboard_metrics`.

Current live-agent experiment slice: `e2e/experiments/agent_ab/` now supports the
Math.NET five-arm assay (`sham`, `oracle_positive`, `enforced_control`,
`blast_radius`, `pebra`). Two one-seed DeepSeek/Math.NET assay runs have completed
cleanly, one sequential and one with parallel arms. Both produced the same valid
structure: sham and blast-radius harmed, oracle-positive completed safely,
enforced-control avoided harm, and PEBRA avoided harm. This is evidence that the
assay is finally sensitive and that PEBRA prevents the destructive Gamma edit in
this setup, but it is not yet a powered efficacy claim: PEBRA avoided harm by
blocking/stopping, not by completing the safer refactor, and the Math.NET corpus
does not yet include a safe task to measure over-caution cost.

Reviewer follow-up tightened the next-run standard. The assay now has a
no-agent `revise_safer` calibration preflight: the harmful MNGAMMA patch must
route to `revise_safer`, the reference correct-fix patch must be non-blocking,
and its expected loss must be lower. The PEBRA arm also surfaces the production
`safer_route` constraints as blinded advisory text, so the next run tests the
deployed safe-edit loop rather than only a generic "narrow it" warning.
That calibration currently blocks the Math.NET task: live C# assess still scores
the harmful and reference Gamma patches at the same file-level risk. Until PEBRA
can distinguish the safer route or the claim is narrowed to stop/block behavior,
another paid live-agent assay is not the right next step.

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

Blinded agent assay experiment (real agents, gated/manual only):

```powershell
nox -s e2e-ab
```

See `e2e/experiments/agent_ab/README.md` for the required environment variables
and the full assay recipe. The recommended assay lane on this machine is the
opt-in parallel-arm path:

```powershell
$env:E2E_AB_MODE="assay"
$env:E2E_AB_PROVIDER="deepseek"
$env:E2E_TEMPLATE_BLUEPRINT_REPO="C:\path\to\mathnet-numerics"
$env:E2E_AB_PARALLEL_ARMS="1"
$env:E2E_AB_MAX_WORKERS="5"
nox -s e2e-ab
```

Leave `E2E_AB_PARALLEL_ARMS` unset to use the slower sequential fallback.

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
