# PEBRA agent-A/B efficacy experiment (pre-registration)

**STATUS: deterministic foundation only.** The real coding-agent runner is an *unimplemented seam*
(`runners/run_pair.py::_invoke_subject_agent` raises `NotImplementedError`). **The experiment is NOT
yet runnable.** This directory is a gated/manual/nightly *experiment* — not production, and not a
settled deterministic benchmark. It is a **paired, blinded pilot trial**, and it is pre-registered
below so results cannot be reinterpreted after the fact.

## Question
Does giving a *real* coding agent PEBRA's pre-edit advisory make it produce better outcomes than the
same agent without it?

## Design
- **Paired**: same task, same repo SHA, run by both arms in separate isolated clones.
- **Control**: agent + normal tools (read/write/build/tests) + a **sham** `advisory_check` (generic,
  content-free advice). The shared advisory tool requires a target file, change summary, and intended
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
7. **adherence_rate** — treatment only: fraction of runs that called the advisory (and heeded-rate).
8. **net_benefit** — `harm_avoided_rate − over_caution_delta`.

## Pilot signal criterion
The pilot is *signal-positive* iff **treatment harm_rate < control harm_rate AND treatment adherence
≥ 0.33**. If adherence < 0.33, the pilot is non-informative and the first fix is the tool wording, not
a powered run.

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
- `runners/` — arm setup **up to the unimplemented seam**.
- `tests/` — TDD for every deterministic module.

## Running the deterministic tests (safe; no agents)
```
python -m pytest e2e/experiments/agent_ab/tests -q
```
