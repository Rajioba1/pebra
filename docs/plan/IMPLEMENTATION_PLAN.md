# PEBRA Implementation Plan

*Companion to `../PEBRA_Architecture.md` and `../PEBRA_Report_Final.md`. Section refs like §14 point
to those docs. This plan mirrors the architecture and defines the build order, module contracts,
tests, and exit criteria to implement every ingredient. Status: plan — not yet implemented.*

---

## 0. How to use this plan

- **One phase at a time.** Each phase has an exit milestone and a required test set. Do not start
  phase *N+1* until phase *N*'s exit criteria and tests are green.
- **`core/` purity + `import-linter` are enforced from commit 1**, not retrofitted. The first thing
  built is the enforcement (§4), so no impure import can ever land in `core/`.
- **Every module has a contract before it has code.** Core modules are pure (typed-in → typed-out);
  controllers orchestrate over ports; adapters own I/O. The test type follows the layer:
  core = unit + property + golden; controller = fake-port; adapter = integration.
- **Mirror, don't drift.** Module names, layers, and phase contents below match §3 and §14 exactly.
  If they ever diverge, the design docs win and this plan is corrected.

---

## 1. Architecture recap (the shape we are building)

```
surfaces:   cli/   mcp_server/   dashboard/        parse input / render output only
                         |
app/        controllers (use cases): assess · accept_risk · verify · record_outcome · learning
                         |  (imports core/ + ports/ only)
core/       PURE ENGINE: scoring · gates · benefit · classifier · guidance · learning math
                         |  (imports pure stdlib + core/ only)
ports/      Protocol contracts  <----- implemented by ----->  adapters/  (git, sqlite, radon, …)
```

**Control flow (every workflow):** `surface → app controller → ports gather evidence → core engine
computes the deterministic result → app stores/audits/renders`. Surfaces never call `core/` directly.

**Internal contract (the IR):** the engine is a function of a normalized input. Frontend builds it,
engine transforms it, backend renders it:

```
frontend (controller + adapters):  request + repo evidence  -> AssessmentInput
engine (core):                     AssessmentInput          -> AssessmentResult
backend (controller + surfaces):   AssessmentResult         -> human card / JSON / MCP / dashboard / SQLite
```

`AssessmentInput` and `AssessmentResult` (§3) are the stable seam that keeps the engine pure,
swappable, and testable in isolation.

---

## 2. Package layout (mirrors the §3 module table)

```
pebra/
├── core/                              # PURE ENGINE — pure stdlib + core/ only
│   ├── constants.py                   # STAGE_MAP, CONSEQUENCE_BEARING_EVENTS, cold-start priors, LOG_LOSS_CLIP_EPS, ChangeKind, RiskMode
│   ├── models.py                      # AssessmentRequest, AssessmentInput, AssessmentResult, dataclasses
│   ├── request_validator.py           ├── candidate_parser.py        ├── query_validator.py
│   ├── change_classifier.py           # ChangeKind classification (AD-27)
│   ├── assessment_builder.py          # pure factory → Assessment (sets action_status=pending, AD-4)
│   ├── weight_resolver.py             ├── benefit_model.py (AD-28)   ├── score_math.py
│   ├── score_normalizer.py            ├── confidence_gate.py         ├── decision_engine.py
│   ├── high_risk_controls.py (AD-26)  ├── explanation_generator.py   ├── model_guidance.py (AD-23)
│   ├── post_assessment_guardrails.py  # pure: pre-fetched data → GuardrailResult (AD-11)
│   ├── apply_snapshot.py (AD-16)      ├── prediction_error.py (AD-15) ├── risk_learning.py (AD-14)
│   ├── snapshot_resolver.py           ├── risk_fact_decay.py (AD-17) ├── promotion_evaluator.py (AD-18)
│   ├── snapshot_reconciler.py         ├── contradiction_gate.py      ├── learning_eval.py (AD-19)
│   ├── topk_composer.py (AD-20)       └── scope_dag_resolver.py (AD-21)
│
├── ports/                             # Protocol contracts only
│   ├── evidence_port.py · blast_radius_port.py · architecture_knowledge_port.py · symbol_diff_port.py
│   ├── change_verifier_port.py · contract_surface_port.py · store_port.py · outcome_port.py
│   ├── calibration_port.py · learning_port.py · sanction_port.py · repository_registry_port.py
│
├── app/                               # CONTROLLERS / use cases — import core/ + ports/ only
│   ├── assess_controller.py · accept_risk_controller.py · verify_controller.py
│   ├── record_outcome_controller.py · learning_controller.py
│
├── adapters/                          # I/O — implement ports; import core/ + ports/ + libs
│   ├── paths.py · repository_registry.py · git_adapter.py · ast_import_graph.py · ast_diff_adapter.py
│   ├── architecture_map.py · git_change_verifier.py · contract_surface.py · radon_adapter.py
│   ├── bandit_adapter.py · codegraph_adapter.py · yaml_config.py
│   ├── sanction_store.py · outcome_logger.py · calibration_store.py · learning_store.py   # flat per §3
│   └── store/db.py                    # only db.py lives under store/ per §3
│
├── cli/        assess.py · accept_risk.py · verify.py · record_outcome.py · dashboard.py
├── mcp_server/ server.py              # pebra_assess / pebra_compare / pebra_accept_risk / pebra_verify / pebra_record_outcome
├── dashboard/  server.py · api.py · ports.py · templates/ · static/
└── benchmarks/ flow/  replay.py · scorecard.py · corpus/ · manifests/ · adapters/{structural,jit,agent}/

examples/login_patch.json              # Phase-0 milestone fixture = spec §10 worked example
```

*(Engines stay as the flat `core/` modules of §3. They group logically as risk / benefit / decision /
guidance / verification / learning, but the file layout mirrors the table — no sub-packages unless the
architecture adds them.)*

---

## 3. Internal contracts (build these dataclasses first, in `core/models.py`)

- **`AssessmentRequest`** — the one canonical request (AD-8): `schema_version`, `task`,
  `candidate_actions[]` (each with `proposed_patch?`, `affected_symbols`, change-type flags), `evidence{}`,
  `thresholds{}`. `pebra_assess` single-action short form builds the same object.
- **`AssessmentInput`** (the IR) — produced by a controller from ports, consumed by the engine:
  validated request + `EvidenceBundle` + `BlastEvidence` + `SymbolDiffEvidence`(+`ChangeKind`) +
  `ArchitectureEvidence` + `BenefitDeltaEvidence` + active snapshot + resolved config + `repo_id`/`repo_root`.
- **`AssessmentResult`** — produced by the engine, rendered by the backend: `recommended_decision`
  (1 of 5), `requires_confirmation`, `action_status`, `risk_mode` (companion field — **not** a 6th
  decision; the decision enum stays exactly 5), `scores{}`
  (`expected_loss`, `expected_utility`, `utility_sd`, `rau`, `edit_confidence`, `risk_budget_used`,
  `benefit_breakdown`, bands), `gates_fired[]`, `high_risk_triggers[]`, `model_guidance_packet`,
  `provenance{}`, `repo_id`, `repo_root`.
- **Port return types** (mirror §3): `EvidenceBundle`, `BlastEvidence`, `SymbolDiffEvidence`,
  `ArchitectureEvidence`, `BenefitDeltaEvidence`, `ActualDiffSummary`, `ContractSurfaceFindings`,
  `RiskSnapshot`, `SanctionEvent`, `RepoMetadata`, `CalibrationData`, `LearnedFacts`.

**Invariant:** the engine reads only `AssessmentInput` and returns only `AssessmentResult`. Anything that
needs git/sqlite/subprocess/a file path arrives *inside* `AssessmentInput`; the engine never fetches.

---

## 4. Cross-cutting setup (do this before any Phase 0 logic)

1. **`pyproject.toml`** — Python runtime deps per §12 (`numpy`, `scikit-learn`, `cryptography`, `mapie`,
   `radon`, `bandit`, `pyyaml`, `fastapi`, `starlette`, `uvicorn`, `jinja2`); `[dependency-groups] dev`
   (`pytest`, `pytest-cov`, `hypothesis`, `syrupy`, `jsonschema`, `ruff`, `mypy`, `import-linter`,
   `nox`, `build`, `twine`, `pre-commit`); `[project.optional-dependencies]`
   `bench` / `bench-szz` / `bench-agent` / `bench-external`, plus `ui-e2e` (`playwright`) for the
   Phase-5d dashboard E2E; `requires-python = ">=3.11"`; `[project.scripts] pebra = "pebra.cli.main:main"`.
   CodeGraph (`@colbymchenry/codegraph`) is a required external runtime graph engine, not a Python
   dependency; installers/runtime checks must verify the `codegraph` command and repo `.codegraph/`
   index before graph-backed assessment proceeds.
2. **`import-linter` contracts** (the enforcement of §2's "one rule"):
   - `core` forbidden from importing `ports`, `adapters`, `app`, `cli`, `mcp_server`, `dashboard`,
     and any of `sqlite3, subprocess, argparse, pandas, scipy, matplotlib, seaborn, datasets,
     pydriller, swebench, fastapi, starlette, uvicorn, jinja2` (this is the §12 list).
   - `numpy`/`sklearn` are additionally blocked in `core` as a **plan-level defense-in-depth** add-on
     (they are runtime deps used only by adapters/benchmarks; the AST-purity test in §4.3 catches them
     regardless). This extends §12; it is not stated there.
   - `app` forbidden from importing `adapters` (must go through ports).
   - **`core` and `adapters` forbidden from importing `dashboard`** — the §12 rule: the dashboard reads
     through store/API readers; nothing in the brain reads back from it. Additionally, `dashboard` is a
     surface and must not import `app`.
   - layers contract: `cli|mcp_server > app > ports > core`; `adapters > core,ports`.
3. **`tests/test_core_stdlib_only.py`** — `ast.walk`s every file in `pebra/core/` and asserts no
   forbidden import. Runs in CI.
4. **`nox`** sessions: `tests`, `lint` (ruff+mypy+import-linter), and a **`core-only`** session that
   installs the base package and asserts the engine imports with zero adapters present.
5. **Skeleton** — empty package dirs with `__init__.py`; `examples/`; `tests/{unit,property,golden,integration}`.

Exit: `lint-imports` passes on empty packages; `nox -s core-only` runs.

---

## 5. Phase-by-phase build (mirrors §14)

Each phase: **Goal/Milestone · Build order · ADs realized · Tests · Stubs/Deferred · Exit.**

### Phase 0 — stdlib-core skeleton → first runnable assessment
**Milestone:** `python -m pebra assess examples/login_patch.json` prints the human card with `core/`
stdlib-only, reproducing expected_loss **0.10**, C3 risk budget **50%**, EU **0.39**, RAU **0.31**,
confidence **0.83**, `proceed` (confirmation required) — i.e. spec §10 / Appendix A.

**Build order (dependency-first):**
1. `core/constants.py`, `core/models.py` (incl. the §3 dataclasses + IR).
2. `core/request_validator.py` → `candidate_parser.py`.
3. `core/change_classifier.py` (AD-27: ChangeKind from `SymbolDiff` rows + thresholds).
4. `core/assessment_builder.py` (pure factory; `action_status=pending`, AD-4).
5. `core/weight_resolver.py` (provenance ladder; equal-weight cold-start fallback).
6. `core/benefit_model.py` (AD-28: `benefit_breakdown → benefit + Var(benefit)`).
7. `core/score_math.py` (event-class floor AD-1; EU; `utility_sd` AD-5; RAU AD-2; edit_confidence;
   blast_score; risk_budget_used) → `score_normalizer.py`.
8. `core/confidence_gate.py` → `core/decision_engine.py` (the gate sequence incl. RAU<0 gate AD-2,
   SD gate AD-3, and gate-10 sanction-resolution logic that reads the **pre-fetched** sanction from
   `AssessmentInput` — the engine never calls a port, AD-26) → `core/high_risk_controls.py`.
9. `core/explanation_generator.py` → `core/model_guidance.py` (AD-23 packet: binding/advisory).
10. Ports used in Phase 0: `evidence_port`, `blast_radius_port`, `symbol_diff_port`, `store_port`,
    `outcome_port`, `calibration_port`, `repository_registry_port`, `sanction_port`.
11. `app/assess_controller.py` (the live pipeline over ports → engine → render/persist),
    `app/accept_risk_controller.py` (creates `sanction_events`, binds to risk profile — AD-26 surface).
12. Adapters: `paths.py` (walk-up + `.pebra/` init + `.gitignore`), `repository_registry.py`,
    `ast_diff_adapter.py`, `ast_import_graph.py` (`d+0.5·t`), `git_adapter.py`,
    `store/db.py` (SQLite WAL, hash-chain via `BEGIN IMMEDIATE`; tables `assessments`,
    `model_guidance_packets`, `sanction_events`), `sanction_store.py`.
13. Surfaces: `cli/assess.py`, `cli/accept_risk.py`; `examples/login_patch.json`.

**ADs realized:** AD-1..AD-5, AD-7..AD-10 (math/gates/floors/cold-start/layering — **AD-6's
config-loader warning is deferred to Phase 2** with `yaml_config.py`), AD-23 (packet rendering),
AD-24 (repo-scoped state), AD-26 (accept-risk surface + sanction store), AD-27 (change classifier),
AD-28 (benefit model).

**Tests:**
- Golden (`syrupy`): the worked example card + canonical JSON reproduce exactly.
- Unit: each gate (parametrized); gate-1 policy fires before gate-3; sanction never overrides gate-1.
- Property (`hypothesis`): probability inputs ∈ [0,1]; benefit monotonicity (worse maintainability
  delta never raises benefit); `utility_sd` cannot narrow below evidence-justified floor.
- `core/` purity test (§4.3) green.
- Hash-chain: insert two assessments → `validate_chain()` passes; mutate a row → fails.
- Controller test with **fake ports** reproduces the worked example end-to-end (no FS/DB/subprocess).

**Stubs/Deferred:** Phase-0 evidence gathering returns cold-start/projected evidence — a minimal
`SymbolDiffProvider` returning cold-start `SymbolDiffEvidence`, and `BenefitDeltaEvidence` with all-zero
deltas + `source_type=projected` (→ `benefit = immediate_benefit`), all passed through `AssessmentInput`.
(Benefit deltas arrive via the evidence path; **there is no separate benefit port in §3**.)
**No learning store, no `apply_snapshot`, no active snapshot** — cold-start priors from `constants.py`
only (state this in code so absence of `apply_snapshot` is intentional). `sanction_store` returns
`None` (no active sanctions) at cold start.

**Exit:** milestone passes; all Phase-0 tests + `import-linter` green.

---

### Phase 1 — autonomy guardrails + verify
**Goal:** `pebra verify` checks the actual diff against the approved envelope and packet (AD-11).
**Build:** `core/post_assessment_guardrails.py` (pure: pre-fetched diff/HEAD/contract findings →
`GuardrailResult`); `app/verify_controller.py`; ports `change_verifier_port`, `contract_surface_port`;
adapters `git_change_verifier.py`, `contract_surface.py`; `cli/verify.py` (the `pebra verify` CLI).
The `pebra_verify` **MCP tool** ships in **Phase 3** with `mcp_server/server.py` (all five tools in one
file per §3); Phase 1 delivers the verify *logic* + CLI only. The Phase 1 exit
(assess→edit→verify loop) is satisfied by the CLI.
Evidence-freshness + actual-diff-drift checks; **post-edit full symbol reclassification (AD-27)** that
escalates if the committed change is more severe than the pre-edit packet; measured post-edit benefit
deltas; guidance-compliance logging; dry-run refactor requirement; pre-commit/PR risk card.
**ADs:** AD-11; AD-27 post-edit; AD-23 enforcement; AD-26 sanction invalidation on drift.
**Tests:** guardrail unit tests (pure); integration with a temp `git init` repo for drift/contract;
verify returns `ask_human` on unmet binding control; sanction invalidates on scope/symbol drift.
**Exit:** assess→edit→verify loop works; out-of-envelope diff is caught.

### Phase 2 — evidence quality enrichment
**Build:** `adapters/yaml_config.py` (`.pebra.yml`; per **AD-6** it must **warn** if
`medium_auto_proceed_requires` is present, never silently accept it), `core/query_validator.py`,
the port `ports/architecture_knowledge_port.py` **then** its impl `adapters/architecture_map.py`
(AD-22: temporary Python AST/import graph until Phase 4 installs CodeGraph as the product graph engine),
AST edge confidence / depth buckets / graph uncertainty / entrypoint signal / import-cycle (AD-12), `adapters/radon_adapter.py`,
`adapters/bandit_adapter.py`.
Wire symbol-level criticality and real benefit deltas (radon MI/complexity, coupling) — replacing
Phase-0 projected stubs.
**ADs:** AD-6 (config-loader warning), AD-12, AD-22. **Tests:** architecture-map freshness (content hash → refresh or
`inspect_first` on rebuild failure), graph incompleteness lowers confidence without inflating blast, and cosmetic edit in a C4 path scores low (symbol evidence beats file membership).
**Exit:** config-driven thresholds + real evidence feed scoring; stale-map handling verified.

### Phase 3 — MCP + outcomes
**Build:** `mcp_server/server.py` (**all five MCP tools**: `pebra_assess`/`pebra_compare`/
`pebra_verify`/`pebra_accept_risk`/`pebra_record_outcome`), `app/record_outcome_controller.py`,
`adapters/outcome_logger.py`
(terminal `action_status`, AD-4), `adapters/calibration_store.py` (shadow read), `cli/record_outcome.py`.
**Note:** outcome recording is **v1** (schema exists from Phase 0; full write path here) — learning,
dashboard trust, and benefit calibration depend on it.
**Tests:** MCP round-trip equals CLI result (one engine, two surfaces); outcome write closes the
`action_status` lifecycle; shadow rows excluded from calibration views.
**Exit:** agent can assess/compare/accept-risk/verify/record-outcome over MCP.

### Phase 4 — CodeGraph graph engine
**Build:** `ports/codegraph_provider_port.py`, `adapters/codegraph_adapter.py`, and the wiring that
replaces Python-only file/import fan-in with CodeGraph-backed symbol graph evidence. CodeGraph is the
required runtime graph backend. The adapter runs `codegraph sync --quiet <repo>`, then
`codegraph status --json <repo>`, and trusts the graph only when initialized, pending changes are zero,
`index.reindexRecommended=false`, and no worktree mismatch is present. It reads
`.codegraph/codegraph.db` in read-only mode with Python `sqlite3` and normalizes CodeGraph `nodes`,
`edges`, `files`, and `project_metadata` into PEBRA evidence.

**PEBRA-owned math:** CodeGraph supplies graph facts only. PEBRA computes symbol fan-in percentiles,
edge-confidence tiers from CodeGraph edge provenance, blast/affected-area summaries, structural
features, and calibration scope. The adapter records `codegraph_version`, extraction version, freshness,
and edge provenance in prediction provenance. The old stdlib AST/import graph becomes test fixture /
emergency development support only, not the product graph path.

**Tests:** missing binary/uninitialized index fails closed; dirty-after-sync status routes to stale
graph handling; reindex-recommended and worktree-mismatch are stale; read-only DB queries compute
symbol fan-in percentile; edge provenance maps to confidence tiers; `core/` import purity remains
unchanged; golden fixture remains byte-identical when it deliberately uses graphless fixture evidence.
**Exit:** PEBRA has platform-agnostic symbol fan-in and graph freshness wired through CodeGraph, with
PEBRA still owning all risk math and learning semantics.

### Phase 5 — calibration + learning loop
**Build:** `LearningPort`, `app/learning_controller.py` (async/batch), `core/prediction_error.py`
(AD-15: residual/Brier/log-loss + continuous metrics for benefit AD-29), `adapters/learning_store.py`,
`core/snapshot_resolver.py`, `core/apply_snapshot.py` (AD-16 — wired into the live pipeline pre-scoring,
read-only), `core/risk_fact_decay.py` (AD-17), `core/promotion_evaluator.py` (AD-18 counterfactual replay),
`core/snapshot_reconciler.py`, `core/contradiction_gate.py`, `core/risk_learning.py` (AD-14),
`core/learning_eval.py` (AD-19 stream eval). Tables `outcomes`, `prediction_errors`, `learned_risk_facts`,
`risk_snapshots`. **Benefit learning (AD-29):** separate prediction targets (`p_recurrence` binary,
`maintainability_delta` continuous), **separate calibration views** (no Brier/MSE mixing), **decoupled
promotion gates** (risk never waits on benefit; benefit matures async; `Var(benefit)` narrows only from
observed benefit outcomes; guidance-influenced rows flagged/excluded). Rolling Brier/log-loss reporter;
flip `shadow_mode=false` when enough data; SWE-bench pilot. Canary/benchmark rows excluded from default
calibration views.
**ADs:** AD-14..AD-19, AD-29. **Tests:** Brier/log-loss/ECE unit tests; `apply_snapshot` scope
precedence + deterministic conflict resolution; a fake snapshot adjustment changes RAU; continuous-vs-binary
calibration kept separate (target_type); two-tier (measurement auto / value+policy human-ratify).
**Exit:** AD-19 stream eval shows calibration improves vs genesis baseline; risk promotion never blocked by benefit.

### Phase 5b — executable product benchmark
**Build:** `benchmarks/flow/replay.py`, `benchmarks/flow/scorecard.py`, corpora + manifests,
deterministic flow regression, structural-agreement adapters, JIT outcome-oracle loaders, chronological
learning-lift replay vs genesis baseline, calibration-improvement report, false-proceed/false-block
scorecard, optional with-vs-without-PEBRA agent efficacy runs (`bench-agent`).
**Tests:** regression mode byte-identical across runs; calibration vs labeled JIT corpus; the
**efficacy A/B** is the survival proof of the uniqueness claim — risk-efficacy first, benefit-efficacy long game.

### Phase 5c — Risk Observatory dashboard
**Build:** `dashboard/{server,api,ports}.py`, `cli/dashboard.py` (the `pebra dashboard` entry point),
templates/static; repo switcher via
machine registry; `/repos/<repo_id>/...` scoping; port reuse/auto-increment/`--instance` (AD-25);
panels overview/assessment/risk/learning/guidance/replay/architecture/audit; localhost bind + bearer +
Host allowlist + CSP nonce; **no benchmark-result panels**; read-only.
**ADs:** AD-25. **Tests:** read-only (no mutate); security posture (Host allowlist, loopback bind).

### Phase 5d — dashboard visual E2E
**Build:** headed Playwright suite (`ui-e2e` group) vs fixture SQLite/API; screenshots/traces;
no-console-error; non-blank chart assertions; replay interaction; stable `data-testid` selectors.
**Note:** Playwright validates *rendering/wiring*, not metric correctness (that is the benchmark's job).

### Phase 6 — v1.5
`core/topk_composer.py` (AD-20 reliability-weighted log pooling), Monte Carlo (numpy), Ed25519 signing
(cryptography), multi-language graphs, `pebra_explain`. MC decision gates activate here.

### Phase 7 — v2
`core/scope_dag_resolver.py` (AD-21 typed scope/action DAG in `risk_snapshots.metrics_json.scope_dag`),
optional `scope_node_id` on facts, deterministic dominance traversal.

---

## 6. Testing & enforcement strategy

| Layer | Test type | Tooling |
|---|---|---|
| `core/` (pure) | unit + property + golden | `pytest` · `hypothesis` · `syrupy` |
| `app/` controllers | fake-port pipeline tests | `pytest` + fake Protocol impls |
| `adapters/` | integration vs fixture repo / temp `git init` / temp SQLite | `pytest` |
| product quality | flow replay + scorecard (calibration, false-proceed/block, efficacy A/B) | `benchmarks/flow` |
| dashboard | headed visual E2E | `playwright` (`ui-e2e`) |

**Mechanical guards (CI, every PR):** `import-linter` layer contracts; the `core/` AST-purity test;
the golden worked-example test; `validate_chain()` tamper test; calibration `target_type` separation
test; the `nox core-only` session.

**Gate to advance:** a phase is "done" only when its exit milestone passes **and** all its tests +
`import-linter` are green. No phase starts before the prior phase's gate is green.

---

## 7. Definition of Done (v1) — maps to §15

`pebra assess` produces the correct card stdlib-only and reproduces the worked example; repo resolution
+ `.pebra/` init work from subdirectories; `core/` import-purity test passes; every decision is
hash-chained and outcomes record; concurrent writes can't fork the chain (`BEGIN IMMEDIATE`); guidance
packets are derived + hash-chained + `pebra_verify`-enforceable; controlled-high-risk approvals are
sanctioned, profile-bound, and drift-invalidated; high-risk routes are never bare (carry
`risk_mode`+`high_risk_triggers[]`+controls); learned facts are append-only and reapplied via
`apply_snapshot`; symbol/scope evidence (not file membership) drives scoring; `pebra_verify` reruns
classification on the actual diff; the benchmark harness replays deterministically and runs the
efficacy A/B; the dashboard is read-only and multi-repo; the Playwright suite passes.

---

## 8. Sequencing risks to hold the line on

- **Never put learning logic in the live assessment path.** `assess_controller` only *reads* the active
  snapshot via `apply_snapshot` (pure, read-only). `learning_controller` is the sole writer, on a
  separate trigger. Different lifecycles; no call relationship.
- **`assessment_builder` stays a pure factory** — it receives gathered evidence, never calls a port.
  The controller is the only orchestrator.
- **`apply_snapshot` runs pre-scoring** (after evidence, before any math) so learned facts adjust every
  score consistently.
- **Benefit calibration is async and decoupled** (AD-29): risk promotion never waits on slow benefit
  outcomes; benefit promotion never recomputes promoted risk calibration; `Var(benefit)` narrows only
  from observed benefit outcomes (no confidence-propagation backdoor).
- **Sanctions invalidate on drift** (AD-26): a sanction is bound to the risk profile it approved;
  `pebra_verify` invalidates it on scope/evidence/symbol-change drift and re-runs the gates.
- **The engine never grows an I/O dependency.** If a `core/` module needs a value that requires I/O,
  the value is added to `AssessmentInput` and supplied by the controller — the engine never fetches.
