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
- The dashboard launches through `pebra dashboard --port 0` and is queried over HTTP, including the
  Risk Observatory read surface for score series, calibration, learning state, and graph fail-soft views.
- The external lane clones a real local repo into `e2e/out/`, initializes CodeGraph there, and validates
  graph-backed assessment over real CodeGraph + dotnet surfaces.

## Current Scope

Current slices:

- `agent_cli_seeded_learning + dashboard_metrics` / Risk Observatory HTTP reads;
- external CodeGraph graph-vs-no-graph DELETE delta;
- external CodeGraph graph-vs-no-graph MODIFY delta;
- real compiler-outcome learning on `avalonia_template`;
- outcome-to-graph diagnostic attribution as provenance.

Current live-agent experiment slice: `e2e/experiments/agent_ab/` now supports the
Math.NET six-arm risky-task assay (`sham`, `oracle_positive`, `enforced_control`,
`blast_radius`, `pebra`, `pebra_graph_repair`). Two one-seed DeepSeek/Math.NET assay runs have completed
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
The `pebra_graph_repair` arm is currently PEBRA plus a repair-context hint; it is
not automatic candidate verification.
That calibration currently blocks the Math.NET task: live C# assess still scores
the harmful and reference Gamma patches at the same file-level risk, and both
route to `revise_safer`. The calibration uses independent fresh stores for the
two patch assessments so the result is not polluted by the revise-attempt
counter. Until PEBRA can distinguish the safer route or the claim is narrowed to
stop/block behavior, another paid live-agent assay is not the right next step.

This does not block product robustness work in other languages. The C# assay is
a mirror for one partial-support language. CodeGraph can expose richer
signature-level fields for other languages, and PEBRA may add a dark-gated
before/after materialization path for those signature-capable languages. That
path must stay off by default, because CodeGraph has no per-file extraction CLI
and the viable implementation is a throwaway tiny-directory index of touched
files. For C#, the honest choices remain partial graph/topology support or a
separate C# semantic provider.

Current semantic boundary:

- Fine-grained CodeGraph semantic diff from before/after signatures is still
  dark/unwired.
- For C#, CodeGraph does not provide method signatures, so PEBRA remains
  topology/visibility/structure-backed, not mathematically or
  signature-semantically aware.
- `revise_safer` can tell the agent that the current route is structurally
  risky, return safer-route constraints, block the write, and reassess a
  narrower candidate. It cannot independently prove that a Lanczos refactor is
  mathematically equivalent unless caller-supplied/test-backed candidate
  verification or the hidden test oracle catches it.

Review workflow: changes to this boundary need independent review across three
paths before a paid run: CodeGraph capability/provenance, `revise_safer` gate
and reassessment behavior, and candidate-verifier/test-oracle semantics. Treat
review findings as hypotheses until they are re-derived from code and pinned by
tests.

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

Without `E2E_UI=1`, the dashboard lane still launches the real server and checks the JSON endpoints over
HTTP. With `E2E_UI=1`, Playwright drives all five dashboard tabs (`overview`, `history`, `calibration`,
`learning`, `graph`) and asserts loaded view markers, no uncaught page errors, and no CSP violations.
The seeded-learning fixture writes more than 100 outcomes, so this lane takes minutes on Windows.

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
- Dashboard API exposes chain, overview, assessment history, score series, calibration bins, learning
  snapshots/facts, and graph fail-soft payloads for the learned run.
- External graph lane proves CodeGraph changed the DELETE and MODIFY assessment by comparing indexed
  and no-graph copies of the same repo/request.
- External compiler lane records a real dotnet build failure, then uses honest seeded history to prove
  promotion and future reassessment consume the learned snapshot.
