# PEBRA agent-A/B efficacy experiment (pre-registration)

**STATUS: run-ready (Phase G), not yet triggered.** The real coding-agent runner is complete and
`AnthropicClient.send` is now LIVE — the `NotImplementedError` stop is gone. The **only** guard against
a run is the fail-closed run gate (`E2E_AB_RUN=1` + `E2E_EXTERNAL=1` + `ANTHROPIC_API_KEY`), and nothing
in-tree opens it, so the experiment does not run under ordinary CI or by accident. Current endpoint is
**build-break + scope** (`build_break_scope`) until per-task evaluator test projects are authored — see
below. This directory is a gated/manual/nightly *experiment* — not production, and not a
settled deterministic benchmark. It is a **paired, blinded pilot trial**, and it is pre-registered
below so results cannot be reinterpreted after the fact.

## Question
Does giving a *real* coding agent PEBRA's pre-edit advisory make it produce better outcomes than the
same agent without it?

## Design
- **Paired**: same task, same repo SHA, run by both arms in separate isolated clones.
- **Control**: agent + normal tools (read/write/build/tests) + a **sham** `advisory_check` (generic,
  PEBRA-content-free advice). The shared advisory tool requires a target file, change summary, and intended
  unified diff so both arms receive the same pre-edit evidence shape.
- **Treatment**: identical, except `advisory_check` is backed by **real PEBRA**.
- **Blinded**: subjects are unbriefed real coding agents. The prompt never mentions PEBRA, an
  experiment, or arm labels; the trap/benign label is hidden. The evaluator knows the oracle only
  after the fact.

### Blinding invariant (load-bearing)
Both arms expose a tool with the **same name (`advisory_check`), same input schema, and same output
shape**. Only the backend content differs. If the name/schema/keys ever differ by arm, the subject
could infer its arm and the trial is unblinded. A transcript **leak scan** flags any run mentioning
experiment/PEBRA/etc.; leaked runs are excluded from the efficacy analysis.

### Never-mutate-source / isolation
The source checkout is never touched; `repo_source.clone_at_recorded_head` clones at a pinned SHA into
gitignored `e2e/out/ab/`. Each subject gets its own clone.

## Pre-registered endpoints (all reported, flattering or not)
1. **harm_rate** — fraction of *risky* runs where harm materialized (build fail, test fail, or scope drift).
2. **harm_avoided_rate** — `control.harm_rate − treatment.harm_rate` (paired).
3. **over_caution_rate** — fraction of *safe* runs the arm failed to complete (needless block).
4. **quality_failure_rate** — fraction of attempted runs whose evaluator build/test failed or was missing.
5. **task_completion_rate** — fraction of runs that edited within the expected scope and passed evaluator checks.
6. **mean_edit_cycles** — write→build iterations (speed / rework proxy).
7. **adherence_rate** — treatment only: fraction of runs that called the advisory; the conclusion uses
   **effective_adherence_rate**, which excludes malformed/unavailable advisory calls.
8. **net_benefit** — `harm_avoided_rate − over_caution_delta`.

## Pilot signal criterion
The pilot is *signal-positive* iff **paired net benefit is positive AND treatment adherence ≥ 0.33**.
Net benefit is `harm_avoided_rate - over_caution_delta`, so a treatment that avoids risky harm but
needlessly blocks safe work does not get a flattering conclusion. If adherence < 0.33, the pilot is
non-informative and the first fix is the tool wording, not a powered run.

## What we will NOT claim
- **No p-values from the pilot.** 3 seeds/arm cannot reach significance; the pilot is directional only.
- **`net_benefit ≤ 0` and net-negative are valid, reportable outcomes** — the report has pre-canned
  "no net benefit" and "tool not adopted (non-informative)" conclusions, shown as prominently as a
  positive result.
- The Wilcoxon p is a normal approximation for context, never a small-n significance claim.
- **Powered analysis should use McNemar's test** (the correct test for *paired binary* outcomes);
  the tie-corrected Wilcoxon-on-booleans here is retained only as the directional pilot statistic.

## Honest modeling decisions (challengeable)
- On a *risky* task, **scope drift counts as harm** (over-editing a risky change is itself a risk).
  The hidden oracle scope includes the known files required for a correct contract-wide fix, so the
  treatment can earn success by fixing the dependent code; refusal is not the only non-harm outcome.
- Any attempted edit must have an evaluator build result. Missing evaluator build after an edit is counted
  as a quality failure, not as success.
- **heeded_guidance is an operational proxy**, not proof of causation (e.g. "ran build before editing"
  after an inspect-first advisory).

## Layout
- `models.py` — dataclasses (pure, stdlib).
- `forbidden.py` — the single shared forbidden-term set for both leak-guards (transcript scanner + corpus loader).
- `corpus/` — `tasks.jsonl` (agent-facing) + `oracles.jsonl` (hidden labels) + `loader.py` (join+validate).
- `tools/` — `advisory_contract.py` (shared shape) + `advisory_check_sham.py` (control) + `advisory_check_real.py` (treatment, via pebra CLI).
- `metrics/` — `oracle.py`, `adherence.py`, `blinding.py`, `scorecard.py` (all pure; the trusted ruler).
- `reports/render_report.py` — scorecard markdown/json.
- `runners/` — `run_gate` (fail-closed gate), `model_client` (Protocol + ScriptedClient + AnthropicClient
  **live, Phase G**), `tool_impl` (7 confined tools + path guard), `agent_loop` (turn loop, capture,
  limits, blinding pre-send check), `evaluator` (post-agent hidden-test injection + build/test),
  `preflight` (oracle-label + graph-freshness gates), `orchestrator` (gated task×arm×seed loop),
  `run_pair` (arm setup + the loop, run only under the gate).
- `tests/` — TDD for every deterministic module (all via ScriptedClient/mock; no LLM, no dotnet).

## Runner status (this slice)
The real-agent runner is COMPLETE and `AnthropicClient.send` is LIVE (Phase G). The `NotImplementedError`
stop is gone, so the **fail-closed run gate** (`E2E_AB_RUN=1` AND `E2E_EXTERNAL=1` AND `ANTHROPIC_API_KEY`)
is the **sole** guard against a run, and nothing in-tree opens it. The deterministic loop is still fully
exercised by `ScriptedClient` (tests never call the live API). A bad/absent key is caught two ways: the
gate requires a non-empty key, and any run that still errors is captured to `SubjectResult.error`,
**excluded from the scorecard**, and the orchestrator **fails fast** on it (so a misconfigured key can't
silently produce a null pilot). Triggering an actual run remains a manual, explicit step.

**Blinding pre-send scan (scope):** the fail-closed check scans only harness-authored strings — the
subject prompt and the advisory tool's OUTPUT — never the agent's file reads/lists/searches. Repo content
in this Avalonia codebase legitimately contains words like "graph"/"oracle"; scanning it would
false-abort real runs.

**Hidden oracle via post-agent test injection:** the agent runs on a clone with NO evaluator tests
visible (cannot read/teach-to/delete them); after it stops and its diff is captured, the orchestrator
injects `corpus/evaluator_tests/<task_id>/` and runs `dotnet build` + `dotnet test`.

**Graph pre-flight (treatment integrity):** before any run, the target must resolve on a FRESH CodeGraph
and graph-backed fields must appear in the treatment assess payload — else fail-closed. Prevents a
stale/missing graph from silently degrading treatment to ~control.

## Honest claim per task
- With an injected evaluator test project → **build + test + scope efficacy**.
- Without one → **build-break + scope efficacy** only.
`corpus/evaluator_tests/` currently holds no task projects, so today's endpoint is build-break + scope;
add a neutral test project per task to upgrade it (never added to the source checkout — injected into the
clone post-agent).

## Corpus and construct validity
Risky tasks are scored as build-break + scope traps, not refusal-only traps. The expected scope for a
contract edit includes the directly-known dependent implementer/caller files so a correct broad fix can
complete successfully in either arm. Unrelated files still count as scope drift.

The oracle preflight validates both directions for every risky task:

- `corpus/oracle_patches/<task>.patch` applies the intentionally risky edit and must fail the build.
- `corpus/correct_fix_patches/<task>.patch` applies the reference correct fix, must touch only
  `expected_edit_scope`, and must build. This proves the widened oracle scope is complete enough to
  reward safe completion, not only refusal.

## Corpus (T2 replaced)
The old T2 (`delete GridSearchAdapter.cs`) was **empirically confirmed to still build** — not a trap. It
is replaced by **"add `int CountMatches(string)` to the `IGridSearchAdapter` interface"**, empirically
confirmed to break the build with **CS0535** on the `GridSearchAdapter<TRow>` implementer (a
contract-break trap invisible from the interface file alone). Oracle patches for all four tasks live in
`corpus/oracle_patches/*.patch` (generated by real edits + `git diff`).

## Running the deterministic tests (safe; no agents)
```
python -m pytest e2e/experiments/agent_ab/tests -q
```

## Running the gated pilot (real agents)

This is the live A/B experiment. It runs the gated preflight first (oracle labels +
fresh graph evidence + C# node-count check), then runs the paired pilot. It needs
CodeGraph on `PATH`, `dotnet`, a local checkout of the external repo, and a valid
Anthropic API key. The `nox -s e2e-ab` session is the explicit run opt-in: it sets
the non-secret gates (`E2E_AB_RUN=1`, `E2E_EXTERNAL=1`) and defaults
`E2E_TEMPLATE_BLUEPRINT_REPO` to sibling `..\avalonia_template` when that checkout
exists. In the normal local setup, you only set `ANTHROPIC_API_KEY`.

Default subject model: pinned Haiku snapshot `claude-haiku-4-5-20251001`.
Override with `E2E_AB_MODEL` only when you intentionally want a different model.
The report records the served model(s) echoed by the API response, not just the
configured request string.

PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="sk-..."
$env:E2E_AB_MODE="smoke"        # smoke|pilot|powered; default: pilot
$env:E2E_AB_RUN_ID="smoke_001"  # optional; default: run_<mode>
nox -s e2e-ab
```

Or store the key in the local ignored file once:

```powershell
New-Item -ItemType Directory -Force .pebra | Out-Null
Set-Content .pebra\agent_ab.env 'ANTHROPIC_API_KEY=sk-ant-...'
# Optional model override, kept local and ignored:
# Add-Content .pebra\agent_ab.env 'E2E_AB_MODEL=claude-haiku-4-5-20251001'
nox -s e2e-ab
```

If your external checkout is not at sibling `..\avalonia_template`, set it explicitly:

```powershell
$env:E2E_TEMPLATE_BLUEPRINT_REPO="C:\path\to\avalonia_template"
```

Bash:

```bash
ANTHROPIC_API_KEY=sk-... \
E2E_AB_MODE=smoke \
E2E_AB_RUN_ID=smoke_001 \
nox -s e2e-ab
```

If your external checkout is not at sibling `../avalonia_template`, add
`E2E_TEMPLATE_BLUEPRINT_REPO=/path/to/avalonia_template`.

`E2E_AB_MODE=smoke` runs one task x one seed x both arms (2 agent runs). Use it as
a cheap live plumbing validation: harness artifacts should not cause scope drift,
the advisory should arrive, adherence should be non-degenerate, and outcomes
should not be another flat tie. It is **not** an efficacy claim. `pilot` runs the
12-run directional trial; `powered` runs the larger configured mode. The run
writes local artifacts under `e2e/out/ab/<run-id>/`, which is ignored by git.
