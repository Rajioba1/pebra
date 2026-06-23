# PEBRA Architecture

*Companion to `PEBRA_Report_Final.md` (the spec). This document maps **how to build** PEBRA: the layering, the module placement, the resolved design decisions, the storage, and the build order. Section references like §7.1 point to the spec.*

*Status: design — for review. Nothing here is implemented yet.*

---

## 1. Purpose

The spec defines **what** PEBRA computes (benefit-risk scores → a 5-way decision). This document defines **how** to implement it: a lean, stdlib-first, hexagonal Python tool whose **pure decision core** is dependency-free and auditable, with all messy I/O pushed to adapters.

It also records **19 Architecture Decisions (AD-1…AD-19)** that resolve gaps the spec left open, and the **human-facing label glossary** so the output is readable by people, not just agents.

**How to read it:** §2–§3 = structure; §4–§10 = the contracts (constants, math, gates, output, post-assessment guardrails, store + learned memory); §11–§12 = what we borrow and what we depend on; §13 = the resolved decisions; §14 = build order; §15 = v1 success criteria.

---

## 2. Hexagonal Architecture Overview

```
            Agent (MCP)            Developer (CLI)
                 |                       |
                 v                       v
        +--------------------+  +------------------+
        |   mcp_server/      |  |      cli/        |   (entry points)
        +---------+----------+  +--------+---------+
                  |                      |
                  v                      v
        +---------------------------------------------+
        |                  ports/                     |   (Protocol interfaces)
        |  EvidenceProvider · BlastRadiusProvider     |
        |  ChangeVerifier · ContractSurfaceProvider   |
        |  StorePort · OutcomePort · CalibrationPort  |
        |  LearningPort                               |
        +----------------+----------------+-----------+
                 ^                         |
   implemented by|                         | called by
                 |                         v
        +--------+---------+   +-----------------------------+
        |    adapters/     |   |           core/             |
        |  ast_import_graph|   |  PURE DOMAIN, stdlib-only   |
        |  sem · codeindex |   |  scoring math · gates ·     |
        |  radon · bandit  |   |  decision engine · explainer|
        |  git · yaml      |   |  constants · models         |
        |  sqlite_store    |   |  (NEVER imports adapters)   |
        +------------------+   +-----------------------------+
```

**The one rule that matters:** `core/` imports **only** stdlib + `core/`. It never imports `adapters/`, `cli/`, `mcp_server/`, or any pip package. Adapters depend on `core/` and `ports/`; `core/` depends on nothing outside the standard library. This is what keeps the decision brain deterministic, testable in isolation, and auditable.

**Why hexagonal:** the value of PEBRA is the *decision math*, which must be reproducible and explainable. Isolating it from I/O (graphs, git, SQLite, MCP) means every number is a pure function of its inputs — an auditor can reconstruct any decision from `core/` alone. It also lets the graph/evidence sources be swapped (built-in AST ↔ sem ↔ codeindex) without touching the brain.

---

## 3. Module → Layer Canonical Table

| Module | Layer | File | Borrows from | License |
|---|---|---|---|---|
| request validator | core | `core/request_validator.py` | stdlib | — |
| candidate parser | core | `core/candidate_parser.py` | stdlib | — |
| query validator | core | `core/query_validator.py` | stdlib | — |
| assessment builder | core | `core/assessment_builder.py` | stdlib | — |
| score math | core | `core/score_math.py` | stdlib `math` | — |
| score normalizer | core | `core/score_normalizer.py` | stdlib | — |
| weight resolver | core | `core/weight_resolver.py` | stdlib | — |
| confidence gate | core | `core/confidence_gate.py` | stdlib | — |
| decision engine | core | `core/decision_engine.py` | stdlib | — |
| explanation generator | core | `core/explanation_generator.py` | stdlib | — |
| data models | core | `core/models.py` | stdlib dataclasses / typing | — |
| prediction error scorer | core | `core/prediction_error.py` | stdlib `math` | — |
| risk learning engine | core | `core/risk_learning.py` | stdlib | — |
| snapshot resolver | core | `core/snapshot_resolver.py` | stdlib | — |
| snapshot reapplication | core | `core/apply_snapshot.py` | stdlib `fnmatch` | — |
| risk fact decay | core | `core/risk_fact_decay.py` | stdlib `math` | — |
| promotion evaluator | core | `core/promotion_evaluator.py` | stdlib | — |
| snapshot reconciler | core | `core/snapshot_reconciler.py` | stdlib | — |
| learning evaluator | core | `core/learning_eval.py` | stdlib | — |
| contradiction gate | core | `core/contradiction_gate.py` | stdlib | — |
| constants / enums | core | `core/constants.py` | — | — |
| EvidenceProvider | ports | `ports/evidence_port.py` | `typing.Protocol` | — |
| BlastRadiusProvider | ports | `ports/blast_radius_port.py` | Protocol | — |
| ChangeVerifier | ports | `ports/change_verifier_port.py` | Protocol | — |
| ContractSurfaceProvider | ports | `ports/contract_surface_port.py` | Protocol | — |
| StorePort | ports | `ports/store_port.py` | Protocol | — |
| OutcomePort | ports | `ports/outcome_port.py` | Protocol | — |
| CalibrationPort | ports | `ports/calibration_port.py` | Protocol | — |
| LearningPort | ports | `ports/learning_port.py` | Protocol | — |
| post-assessment guardrails | core | `core/post_assessment_guardrails.py` | stdlib | — |
| ast import graph (default blast) | adapters | `adapters/ast_import_graph.py` | **codeindex** `impact.py` (`d+0.5·t`) | Apache-2.0 |
| sem adapter (optional) | adapters | `adapters/sem_adapter.py` | subprocess | MIT/Apache-2.0 |
| codeindex adapter (optional) | adapters | `adapters/codeindex_adapter.py` | subprocess | Apache-2.0 |
| git change verifier | adapters | `adapters/git_change_verifier.py` | GitNexus `detect_changes` concept | PolyForm NC reference-only |
| contract surface scanner | adapters | `adapters/contract_surface.py` | GitNexus `api_impact`/`route_map`/`tool_map`/`shape_check` concepts | PolyForm NC reference-only |
| radon adapter (optional) | adapters | `adapters/radon_adapter.py` | radon | MIT |
| bandit adapter (optional) | adapters | `adapters/bandit_adapter.py` | bandit | Apache-2.0 |
| git adapter | adapters | `adapters/git_adapter.py` | subprocess git | — |
| yaml config | adapters | `adapters/yaml_config.py` | pyyaml (opt) / stdlib | MIT |
| sqlite store | adapters | `adapters/store/db.py` | **codeindex** `store/db.py` + **Aegis** hash-chain idiom | Apache-2.0 / pattern |
| outcome logger | adapters | `adapters/outcome_logger.py` | sqlite store | — |
| calibration store | adapters | `adapters/calibration_store.py` | sqlite store | — |
| learning store | adapters | `adapters/learning_store.py` | sqlite store | — |
| `pebra assess` / `verify` / `record-outcome` | cli | `cli/*.py` | stdlib argparse | — |
| `pebra_compare` / `pebra_assess` / `pebra_verify` / `pebra_record_outcome` | mcp_server | `mcp_server/server.py` | MCP stdio (codeindex pattern) | — |

---

## 4. Canonical Constants & Vocabulary  (`core/constants.py`)

**Decision enum (exactly 5):** `proceed · inspect_first · test_first · ask_human · reject`. Companion fields (NOT decisions): `requires_confirmation: bool`, `action_status: pending|completed|skipped|rejected`.

**STAGE_MAP** (spec §2.7) — ordinal stage → cardinal value; the raw stage is never multiplied, only mapped:
`C0→0.10 · C1→0.30 · C2→0.50 · C3→0.80 · C4→1.00`

**CONSEQUENCE_BEARING_EVENTS** (see AD-1): `{public_api_break, security_sensitive_change, external_state_damage, migration_failure, dependency_break, api_contract_break, route_behavior_break, tool_schema_break, response_shape_mismatch, consumer_shape_mismatch}`. The criticality floor applies **only** to these.

**Human-facing label glossary (canonical):**

| Technical | Human label | Where shown |
|---|---|---|
| `risk_budget_used` | **Risk Level** | card (as band) |
| `risk_adjusted_utility` (RAU) | **Value After Risk** (formal: *Risk-Adjusted Value*) | card (as band) — **never shown as "RAU"** |
| `expected_loss` | **Expected Damage** | card (rounded) |
| `criticality` | **Code Sensitivity** | card |
| `blast_radius` | **Affected Area** | card **Why** block only — *not* a verdict bar (matches spec §2 label table) |
| `edit_confidence` | **Confidence** | card (band + %) |
| `p_success`, `p_event`, `disutility`, `utility_sd`, weights, provenance | — | **JSON only** |

Cold-start priors (AD-9) and default variances (AD-5) also live here, all tagged `prior_uncalibrated`.

---

## 5. Scoring Math  (`core/score_math.py` — pure, stdlib `math` only)

```
disutility_j = max(elicited_j, STAGE_MAP[stage])   # ONLY if event ∈ CONSEQUENCE_BEARING_EVENTS
             = elicited_j                            # otherwise (event-class-aware floor, AD-1)

expected_loss   = Σ_j  p_event_j · disutility_j                       # §5.5
expected_utility= p_success · benefit − expected_loss − review_cost   # §7.1
utility_sd      = sqrt( benefit²·Var(p_success) + p_success²·Var(benefit)
                        + Var(review_cost) + Σ event_variance + scenario_variance )  # §7.2, first-order
RAU             = expected_utility − 1.28 · utility_sd                # §7.1 (z=1.28, 90% lower bound)
edit_confidence = exp( Σ_i w_i · ln(x_i) )   over 6 factors, w_i=1/6  # §7.4 weighted geometric mean
risk_budget_used= expected_loss / effective_threshold                # bounded risk %; >1.0 = over budget
blast_score     = direct + 0.5 · transitive   (normalized to [0,1])  # codeindex impact.py:62
```

Every output is wrapped with provenance (`source_type=derived, provider=pebra, formula=…`). No randomness, no model calls in v1 → fully reconstructable.

**BlastRadiusProvider v1 output contract** (priority-2 evidence enrichment):

```text
BlastEvidence {
  direct_count,
  transitive_count,
  depth_buckets: {1: count, 2: count, 3: count},
  edge_confidence_mean,
  edge_confidence_min,
  low_confidence_edge_count,
  entrypoint_signal,
  import_cycle_detected
}
```

`depth_buckets` keeps "one direct caller" distinct from "many distant dependents"; direct depth-1 callers raise event probability more strongly than distant transitive reach. `edge_confidence_*` feeds `evidence_quality` and `source_reliability`: a blast estimate built on wildcard/dynamic imports lowers confidence instead of pretending every edge is equally reliable. `entrypoint_signal` raises criticality or affected-area explanation for route handlers, CLI entrypoints, MCP tools, `main`/`run`/`handle_*` style functions, and exported public functions. `import_cycle_detected` is a structural flag that can trigger `inspect_first` or `test_first`.

Clean-room heuristic defaults for the stdlib AST adapter:

```text
static resolved import edge      ≈ 0.85
relative import edge             ≈ 0.75
wildcard import edge             ≈ 0.35
dynamic string/importlib edge    ≈ 0.15
unknown/unresolved edge          ≈ 0.10
```

These are not calibrated probabilities. They are evidence-confidence weights, tagged `source_type=estimated` until outcome calibration can fit them.

---

## 6. Decision Gate Sequence  (`core/decision_engine.py` — §8 is the SOLE authority)

Ordered; first match wins:

1. policy violation → **reject**
2. `criticality_stage == C4` and `c4_always_ask_human` → **ask_human** (`requires_confirmation=true`)
3. `expected_loss > effective_threshold` → **ask_human** (or **reject** if `expected_utility < 0`)
4. **`RAU < 0` → reject** (default) / ask_human if `ask_on_negative_rau` configured  *(AD-2 — now a formal numbered gate in spec §8.2)*
5. not MC and `utility_sd > max_utility_sd_without_human` and `expected_utility > 0` → **ask_human**  *(AD-3: EU<0 already handled by gate 3/4)*
6. MC available and `P(utility<0) > max_p_negative_utility` → ask_human/reject *(v1.5)*
7. `decision_instability > threshold` → **inspect_first** / **test_first**
8. `edit_confidence < low_edit_confidence` → inspect_first / test_first / ask_human / reject
9. confidence-upgrade requested without `evidence_delta` → reject
10. else → **proceed** (set `requires_confirmation=true` if C3)

**Double-count guard:** `criticality_stage` feeds the disutility floor and threshold tightening **only** — never `p_event`. `blast_radius`/usage feeds `p_event`. The raw C-stage is never multiplied.

Confidence state machine (§8.1): low → gather evidence; medium → cheap evidence + re-score, auto-proceed only on upgrade; high → proceed. Retrieval-only upgrades capped at `max_retrieval_only_confidence`.

---

## 7. Assessment Object  (`core/models.py`, spec §11.1)

In-flight bag passed through the pipeline:
`{ schema_version, request, candidate_actions[], evidence{}, scores{}, thresholds{}, gates{}, decision, provenance{} }`.

**`action_status` ownership (AD-4):** `assessment_builder` sets `pending` on creation; terminal states (`completed`/`skipped`/`rejected`) are written **only** by `outcome_logger` via `OutcomePort` (triggered by `pebra_record_outcome`). The decision engine may read but never write it.

---

## 8. Output — Dual Surface

One source of truth (the scored `ActionResult`), two views.

**Human card (default)** — plain language, jargon demoted:
```
PEBRA Decision: Proceed (confirmation required)

Risk Level:        Moderate          (used 50% of the safe limit)
Confidence:        High (83%)
Value After Risk:  Positive
Code Sensitivity:  High — sensitive auth code
Expected Damage:   0.10

Why:
  - Touches auth code (sensitive).
  - Affected Area: small — affects ~2 files / few call sites (measured).
  - Expected damage is within the safe limit.
  - The benefit still clears the risk after uncertainty.
```
Rules: **"RAU" is never printed** — only "Value After Risk" as a band (Negative/Borderline/Positive/Strong). **Risk Level** is a band (Low/Moderate/High/Critical), not a float. **Affected Area** appears in *Why* as a measured fact, never a verdict bar. `requires_confirmation` shows only on `proceed` (as the "(confirmation required)" suffix) — omitted on all other decisions (AD-7).

**Canonical JSON (agent + audit):** the full §9.1 schema — every score object `{value, level, source_type, provider, confidence, evidence[], method}`, the raw `rau`/`expected_utility`/`utility_sd`, `floor_applied`, `weight_source`, `thresholds_used`, `action_status`. `--json` (CLI) or the MCP result payload.

---

## 9. Post-Assessment Guardrails  (`core/post_assessment_guardrails.py`)

This is the GitNexus-inspired priority-1 addition: PEBRA does not only approve an edit before it happens. In autonomy mode, PEBRA also verifies that the agent stayed inside the approved envelope before committing, opening a PR, or recording a successful outcome.

Priority order:

1. **Evidence freshness check** — compare the assessment's evidence commit to current `HEAD`. If `HEAD` changed, mark `evidence_freshness=stale` and require re-assessment (`inspect_first`) before autonomous proceed.
2. **Actual diff vs planned scope check** — compare the real git diff to the candidate action's `expected_files`, `affected_symbols`, dependency flags, and migration/schema flags. If the agent touched unexpected files, lockfiles, schemas, migrations, or broad call surfaces, set `scope_drift_detected=true` and route to `ask_human` or `reject`.
3. **Contract surface change check** — detect public API, route handler, MCP/RPC tool schema, exported symbol, response-shape, and consumer-shape changes. These create event candidates such as `api_contract_break`, `route_behavior_break`, `tool_schema_break`, `response_shape_mismatch`, and `consumer_shape_mismatch`.
4. **Dry-run refactor check** — for `rename`, `public_api_change`, `dependency_upgrade`, and `broad_refactor`, require an impact preview before editing. Without a preview, default to `inspect_first`.
5. **Pre-commit / PR risk card** — render the same human card at the commit/PR boundary, including any stale-evidence, scope-drift, or contract-surface findings.

Guardrail result shape:

```text
PostAssessmentGuardrails {
  evidence_freshness: fresh | stale | unknown,
  assessed_commit,
  current_head,
  scope_drift_detected,
  unexpected_files[],
  contract_surface_changes[],
  dry_run_required,
  pre_commit_decision,
  reasons[]
}
```

Decision integration: these checks run after an edit and before commit/PR. They do not replace the §6 decision engine; they feed it new evidence and may force a re-score. Hard failures map to existing decisions only: `inspect_first`, `test_first`, `ask_human`, or `reject`. There is no new decision enum.

Invocation:

```text
pebra verify --assessment-id <id> [--scope staged|all|branch]
pebra_verify({ assessment_id, scope })
```

`verify` loads the stored assessment, compares the current diff against the approved action envelope, runs the post-assessment guardrails, writes a `post_assessment_guardrails` row, and returns the same human card plus canonical JSON. `record-outcome` remains the terminal outcome logger; it should refuse a successful autonomous outcome if the latest `verify` result is stale or failing.

This is the autonomy-envelope rule: **PEBRA may let an agent proceed on a branch, but only if the final diff still matches the approved risk envelope.**

---

## 10. SQLite Store  (`adapters/store/db.py`)

WAL mode, foreign keys on, schema versioned. **JSON-as-truth:** the full request/response blobs are the source of truth; relational columns are projections (rebuildable). `PRAGMA integrity_check` on open → delete+rebuild cache if corrupt.

```sql
assessments(id PK, task, schema_version, request_json, response_json,
            recommended_decision, created_at,
            assessed_commit, evidence_freshness,
            risk_snapshot_id, prediction_error_model_id,
            previous_hash, integrity_hash,        -- SHA-256 chain (stdlib hashlib)
            shadow_mode INTEGER DEFAULT 1)         -- day-one logging; excluded from calibration
outcomes(id PK, assessment_id FK, action_id, terminal_status, actual_result,
         recorded_at, previous_hash, integrity_hash)
post_assessment_guardrails(id PK, assessment_id FK, current_head,
                           scope_drift_detected, unexpected_files_json,
                           contract_surface_changes_json, pre_commit_decision,
                           reasons_json, recorded_at,
                           previous_hash, integrity_hash)
prediction_errors(prediction_id PK, assessment_id FK, action_id,
                  risk_snapshot_id, prediction_error_model_id,
                  target, calibration_bucket, predicted_probability,
                  actual_outcome, residual, brier_error, log_loss,
                  outcome_label_status, calibration_scope,
                  recorded_at, previous_hash, integrity_hash)
learned_risk_facts(fact_id PK, fact_type, scope, statement,
                   value_json, evidence_count, degrees_of_freedom,
                   confidence, source_metric, status,
                   base_weight, decay_strength, effective_weight,
                   scope_change_count, last_confirmed_at,
                   rationale, promotion_delta_brier, promotion_delta_log_loss,
                   requires_human_ratification INTEGER,
                   created_from_snapshot, created_at,
                   previous_hash, integrity_hash)
risk_snapshots(snapshot_id PK, parent_snapshot_id, status,
               created_from_outcome_hash, promotion_reason,
               rollback_reason, metrics_json, drift_score,
               rebuilt_from_ledger_at, created_at, activated_at,
               previous_hash, integrity_hash)
criticality_cache(path_pattern PK, criticality_stage, source_type, cached_at)
VIEW calibration_data AS  -- observed prediction rows only; censored/counterfactual rows are excluded
  SELECT ...
  FROM prediction_errors
  JOIN assessments ON assessments.id = prediction_errors.assessment_id
  WHERE assessments.shadow_mode = 0
    AND prediction_errors.outcome_label_status = 'observed'
    AND prediction_errors.calibration_scope = 'proceeded_edits_only'
```

**Hash-chain rule** (idiom from Aegis, reimplemented in stdlib `hashlib` — no code copied):
For **assessments**: `integrity_hash = sha256(canonical({id, created_at, recommended_decision, sha256(request_json), sha256(response_json), previous_hash}))` — it covers `sha256(response_json)`, the canonical response that is the source of truth, not just the request. For **outcomes**: `integrity_hash = sha256(canonical({id, assessment_id, action_id, terminal_status, actual_result, recorded_at, previous_hash}))`. For **post-assessment guardrails**: `integrity_hash = sha256(canonical({id, assessment_id, current_head, scope_drift_detected, sha256(unexpected_files_json), sha256(contract_surface_changes_json), pre_commit_decision, sha256(reasons_json), recorded_at, previous_hash}))`. Learning rows (`prediction_errors`, `learned_risk_facts`, `risk_snapshots`) use the same append-only `previous_hash`/`integrity_hash` pattern. `previous_hash` = prior row's `integrity_hash`. `validate_chain()` re-walks rows and recomputes. Tamper-evident with zero deps.

**Shadow mode** is the cold-start answer: v1 logs every assessment with `shadow_mode=1`; the agent/human proceeds normally; outcomes accumulate; when there's enough data, flip to `shadow_mode=0` and the `calibration_data` view feeds v1.5 calibration — **no re-architecting.**

**Learning-memory split:** raw prediction rows record what PEBRA predicted; outcome rows record what happened; `prediction_errors` record how wrong each observable probability was; `learned_risk_facts` record what PEBRA inferred from many errors; `risk_snapshots` decide which learned facts and parameter sets are active. Learned knowledge is never overwritten in place: a new fit creates a candidate snapshot, shadow/canary gates evaluate it, and promotion only advances the active snapshot pointer.

Examples of learned facts:

```text
dependency_upgrade:major has signed_bias +0.16 over 84 observed outcomes
wildcard import edges have low reliability in this repo
auth patches with targeted tests have high p_success
src/api/billing/** repeatedly produces high-loss contract breaks
```

Safe measurement facts can be auto-promoted after gates pass. Value/policy facts (`criticality` bumps, threshold changes, business-damage changes) stay `status=candidate` with `requires_human_ratification=1`.

**Prediction target vocabulary:** `prediction_errors.target` uses exact canonical names: `p_success` or `p_event.<event_class>` such as `p_event.dependency_break`, `p_event.public_api_break`, or `p_event.response_shape_mismatch`. It never stores human labels.

**Core purity for learning:** `core/prediction_error.py`, `core/risk_learning.py`, and `core/snapshot_resolver.py` are pure functions. They receive already-loaded rows or dataclasses and return candidate facts, metrics, or active snapshot decisions. They do not import `sqlite3`, read files, call git, or mutate the active snapshot pointer. All persistence, row loading, hash-chain writes, and active-snapshot updates live in `adapters/learning_store.py` behind `LearningPort`.

**Snapshot reapplication read path:** `core/apply_snapshot.py` is the pure function that makes learned facts affect the next edit:

```text
apply_snapshot(raw_inputs, active_snapshot, promoted_facts) -> adjusted_inputs
```

It runs after request validation and evidence collection but before score normalization, expected loss, RAU, edit confidence, variance propagation, and gates. It may adjust measurement inputs (`p_success`, `p_event.*`, `source_reliability`, `evidence_quality`, variance, repo-risk priors). It must not rewrite formulas, mutate the active snapshot, silently lower criticality, or auto-apply unratified value/policy facts.

Conflict resolution is deterministic: most-specific scope wins (`symbol > file/path glob > dependency > action_type > global`). If two active facts have the same specificity, the newest active fact wins. Weighted blending is deferred until the spec defines a calibrated method.

**Decay-by-weight, not deletion:** learned facts are append-only, but `apply_snapshot` uses their `effective_weight`, not raw `base_weight`. `risk_fact_decay.py` computes effective weight from repo churn in the fact's scope, confirming outcome count, and prior Brier/log-loss contribution. Decay changes recall strength; it does not rewrite or delete the ledger row.

**Counterfactual promotion and reconciliation:** `promotion_evaluator.py` replays historical assessments with and without a candidate learned fact and compares Brier/log-loss. `snapshot_reconciler.py` periodically rebuilds a candidate snapshot from the raw append-only ledger and compares it with the active snapshot. If drift is too high, promotion freezes and requires review.

**Learning-loop evaluation:** `learning_eval.py` streams historical assessments in chronological order and compares active learned snapshots against a genesis/no-learning baseline. This proves whether the learning loop improves calibration instead of merely accumulating facts.

---

## 11. Reference Patterns & Attributions

| Pattern | Source | License | Use |
|---|---|---|---|
| SQLite WAL store, schema versioning, soft-delete | codeindex `store/db.py` | Apache-2.0 | adapt pattern (Apache-2.0 permits direct reuse with attribution + NOTICE if we choose to copy) |
| Blast-score `d + 0.5·transitive`, BFS over reverse import graph | codeindex `impact.py:62` | Apache-2.0 | adapt pattern / implement compatible adapter |
| MCP stdio JSON-RPC (`TOOLS` list + `_HANDLERS` dict + `serve()`) | codeindex `mcp_server.py` | Apache-2.0 | adapt pattern |
| Per-language analyzer registry | codeindex `analyze.py` | Apache-2.0 | adapt pattern |
| `gate_check` UX: verdict + `reasons[]`, human-first / JSON-under, cycle detection as hard signal | code-impact-mcp | MIT | adapt UX pattern (upgrade 3-way → 5-way) |
| Hexagonal core/ports/adapters, fail-open hook, `_meta` envelope | Ctxo | MIT | adapt pattern (reimplement in Python) |
| Staleness check, `detect_changes`, route/tool/shape impact, dry-run rename guardrails | GitNexus | PolyForm Noncommercial | **reference only** — clean-room concepts, no code copied |
| Decay-aware memory and forgetting curve | SAGE | paper | decay-by-weight only; no LLM self-mutation |
| Governed memory, temporal grounding, reconciliation | SSGM | paper | validation, decay, ledger reconciliation concepts |
| Streaming memory evaluation | Evo-Memory / memory survey | paper | learning-loop replay and no-learning baseline |
| SHA-256 `previous_hash`/`integrity_hash` chain idiom | Aegis | **pattern only** | reimplement in stdlib `hashlib`; *verify Aegis license before copying any code* |
| MCDA math (CRITIC/Entropy/ROC/BWM) | pyDecision | **GPL-3** | **reference only** — clean-room from published formulas, never import |
| Blast-radius provider (multi-lang) | sem (external binary) | MIT/Apache-2.0 | consume via subprocess |

**Correction (important):** **Aegis is a remote gateway / transport, not a local store.** Do not cite it for the store pattern — that comes from codeindex/Ctxo. Aegis contributes **only** the hash-chain integrity idiom, which we reimplement with stdlib.

**GitNexus boundary (important):** GitNexus is PolyForm Noncommercial in `references/`, so it is **not** a dependency and no source should be copied. PEBRA only adopts the architectural lessons: fresh evidence, diff drift verification, contract surface checks, dry-run refactor previews, depth-aware impact, and confidence-tagged graph edges.

**Agent-memory research boundary:** SAGE, SSGM, Evo-Memory, and the memory survey are used as design references only. PEBRA adopts deterministic decay, validation, reconciliation, and replay evaluation. It rejects LLM-written gate parameters, RL memory policies, embeddings in core, and self-mutating formulas.

---

## 12. Dependency Tiers

- **v1 core — zero pip deps (stdlib only):** `ast, sqlite3, hashlib, json, dataclasses, typing, math, subprocess, pathlib, argparse, logging, fnmatch, uuid, tomllib`. Target (not yet implemented; to verify at Phase 0): `pebra assess <file>` runs and prints a risk card with **no `pip install`**.
- **v1 optional extras (graceful fallback if absent):** `radon` (complexity), `bandit` (SAST), `pyyaml` (`.pebra.yml`), `sem`/`codeindex` (external binaries → richer blast radius). Missing tool → adapter returns `None` → falls back to built-in AST blast.
- **v1.5 extras:** `scikit-learn`+`numpy` (calibration, Monte Carlo), `cryptography` (optional Ed25519 signing), `mapie` (conformal intervals).
- **Hard rule:** no GPL/AGPL runtime deps, ever. pyDecision stays reference-only.

**Explicit non-goals for v1:** no GitNexus-style graph database, Cypher, embeddings, web UI, auto-wiki, multi-repo contract registry, full PDG/taint engine, or LLM cluster enrichment. If needed later, those arrive as optional adapters, never inside `core/`.

---

## 13. Architecture Decisions (resolved)

- **AD-1 — Event-class-aware criticality floor.** Define `CONSEQUENCE_BEARING_EVENTS` in `constants.py`; the floor `max(elicited, STAGE_MAP[stage])` applies only to those events. `test_regression`/`review_burden` keep their elicited disutility. *Reproduces the worked example: `test_regression` stays 0.40, `expected_loss=0.10`, `EU=0.39`.* Enforced in `score_math._apply_floor`. **(Spec updated — §5.5 now states the event-class-aware floor.)**
- **AD-2 — `RAU < 0` is a formal gate** (not just a band), placed after the expected-loss gate → `reject` by default (`ask_human` if `ask_on_negative_rau`). Enforced in `decision_engine`. **(Spec updated — now a numbered gate in §8.2, with `ask_on_negative_rau` in §12.)**
- **AD-3 — SD gate keeps `expected_utility > 0`.** The EU<0 case is already covered by gates 3/4, so the SD gate (gate 5) handles only the "positive mean, wide downside" case. Not a bug; documented in code.
- **AD-4 — `action_status` ownership:** `assessment_builder` writes `pending`; `outcome_logger` writes terminal states via `OutcomePort`. Nothing else writes it.
- **AD-5 — `utility_sd` inputs:** resolve each input's variance by **precedence** — (1) explicit variance supplied with the input (as the §10 worked example does), (2) confidence-derived `((1−confidence)/2)²`, (3) cold-start `DEFAULT_VARIANCE` in `constants.py`. The §10 example uses explicit variances (breakdown sums to 0.0036 → `SD=0.06`); the confidence mapping and cold-start defaults are fallbacks and are *not* expected to reproduce that exact SD. **(Spec updated — §7.2.)**
- **AD-6 — `medium_auto_proceed_requires` dropped from v1** (its flags `targeted_checks_pass`/`residual_blast_radius_low`/`no_policy_violation` were undefined). The medium-band re-score + existing gate sequence already cover the intent. Key marked `# v1.5 reserved`; loader warns if present. **(Spec updated — §12 marks the key v1.5-reserved.)**
- **AD-7 — `requires_confirmation` is proceed-only.** Meaningful (and shown) only with `proceed`; set `false` and omitted from the card on every other decision (you can't "confirm" an `ask_human`).
- **AD-8 — One canonical request schema.** `pebra_compare` takes the full §3.1 multi-action request; `pebra_assess` is a single-action short form that builds the same `AssessmentRequest`. No second schema.
- **AD-9 — Cold-start defaults** (`COLD_START_PRIORS`, `COLD_START_P_SUCCESS`, `COLD_START_BENEFIT`, `COLD_START_DISUTILITY`) in `constants.py`, keyed by action class, all tagged `prior_uncalibrated`. Anchored to the spec worked example so a fresh install produces sane numbers without elicitation.
- **AD-10 — Hexagonal layer assignment** is the §3 table (core / ports / adapters / cli / mcp_server), authoritative.
- **AD-11 — Post-assessment guardrails are priority-1 for autonomy.** Evidence freshness, actual-diff drift, contract-surface change, dry-run refactor, and pre-commit risk-card checks ship before heavy graph features. They verify the agent stayed inside the approved envelope.
- **AD-12 — Evidence enrichment is priority-2.** Edge confidence, depth buckets, entrypoint signals, and import-cycle detection improve `evidence_quality`, `source_reliability`, `p_event`, and human explanation, but they must remain stdlib-first and adapter-owned.
- **AD-13 — PEBRA does not become GitNexus.** Graph DBs, embeddings, Cypher, web UI, multi-repo registry, and PDG/taint engines are deferred or adapter-only. PEBRA's product surface is the decision controller and autonomy governor, not the graph platform.
- **AD-14 — Autonomous risk learning is snapshot-gated.** PEBRA may autonomously update measurement parameters (`p_success`/`p_event` calibration, source reliability, edge-confidence weights, variance estimates, repo risk memory) only by deterministic recomputation over the outcome store. It may not mutate a live decision mid-flight: each assessment pins `risk_snapshot_id` and `prediction_error_model_id`; candidates are evaluated as `shadow`/`canary`; promotion advances the active snapshot pointer; rollback points back to the prior snapshot. LLM-authored self-mutation of gate-driving scores, thresholds, or formulas is a non-goal. **(Spec ratified — §12.1–§12.3.)**
- **AD-15 — Prediction error uses proper scoring rules and two-tier routing.** PEBRA learns from observable probability errors, not directly from RAU. Every `p_success` and `p_event_j` prediction gets a `prediction_id`; after outcomes, `prediction_error.py` computes `residual = actual - predicted`, `brier_error = residual^2`, and log loss. These errors feed Tier-1 autonomous calibration. RAU/risk-budget/confidence/decision errors are diagnostics only: first correct measurement error, then treat residual value/policy patterns as human-ratified suggestions. Outcome labels (`observed`, `censored`, `counterfactual`) and calibration scope (`proceeded_edits_only`, `shadow`, `canary`, `benchmark`) must be stored separately to avoid selective-label self-reinforcement. **(Spec ratified — §12.1–§12.3.)**
- **AD-16 — Snapshot reapplication / prior resolution.** Learning affects the next edit only through `apply_snapshot(raw_inputs, active_snapshot, promoted_facts) -> adjusted_inputs`, which runs before scoring and gates. It matches promoted learned facts to the new action by scope, applies deterministic precedence (`symbol > file/path glob > dependency > action_type > global`; newest fact wins within the same specificity), and adjusts only measurement inputs unless a value/policy fact has human ratification. The scoring formulas remain unchanged. **(Spec ratified — §12.4.)**
- **AD-17 — Learned facts decay by recall weight, never by deletion.** PEBRA keeps the append-only fact ledger intact, but `apply_snapshot` uses `effective_weight = base_weight * exp(-scope_change_count / decay_strength)`. `scope_change_count` is churn in the fact's scope, not wall-clock time. High-evidence facts decay more slowly; stale facts fade without losing auditability. **(Spec ratified — §12.5.)**
- **AD-18 — Promotion requires counterfactual replay and reconciliation.** A learned fact is promoted only if replaying historical assessments with the fact improves or does not regress Brier/log-loss relative to replaying without it. Snapshots are periodically rebuilt from the raw ledger and compared to the active snapshot; excessive drift freezes promotion and routes to review. **(Spec ratified — §12.6.)**
- **AD-19 — Learning-loop value must be evaluated as a stream.** PEBRA validates learning by replaying assessments chronologically, comparing active learned snapshots against a genesis/no-learning baseline, and reporting calibration improvement, false-proceed drift, contradiction rate, staleness distribution, and rework efficiency. **(Spec ratified — §14.4.)**

---

## 14. Build Sequence

- **Phase 0 — zero-dep skeleton → first runnable milestone.** `models.py`, `constants.py`, `score_math.py`, `score_normalizer.py`, `request_validator.py`, `candidate_parser.py`, `assessment_builder.py`, `decision_engine.py`, `explanation_generator.py`, core-facing ports used by Phase 0 (`EvidenceProvider`, `BlastRadiusProvider`, `StorePort`, `OutcomePort`, `CalibrationPort`), `adapters/ast_import_graph.py`, `adapters/git_adapter.py`, `adapters/store/db.py`, `cli/assess.py`.
  **Milestone:** `python -m pebra assess examples/login_patch.json` prints the human card — **zero pip installs.**
- **Phase 1 — autonomy guardrails first:** `post_assessment_guardrails`, `change_verifier_port`, `contract_surface_port`, `git_change_verifier`, `pebra verify`, `pebra_verify`, evidence freshness check, actual-diff drift check, dry-run refactor requirement, pre-commit/PR risk card.
- **Phase 2 — evidence quality enrichment:** `yaml_config`, `confidence_gate`, `weight_resolver`, `query_validator`, AST edge confidence, depth buckets, entrypoint signal, import-cycle detection, optional `radon`/`bandit`.
- **Phase 3 — MCP + outcomes:** `mcp_server` (`pebra_compare`/`pebra_assess`/`pebra_record_outcome`), `outcome_logger`, `calibration_store` (shadow read), `cli/record_outcome`.
- **Phase 4 — optional tool adapters:** `sem`, `codeindex`; evidence registry fallback order codeindex → sem → ast.
- **Phase 5 — calibration + learning loop:** `LearningPort`, `prediction_error.py`, `learning_store`, `snapshot_resolver`, `apply_snapshot`, `risk_fact_decay`, `promotion_evaluator`, `snapshot_reconciler`, `contradiction_gate`, `risk_learning`, raw `prediction_errors`, `learned_risk_facts`, `risk_snapshots`, rolling Brier/log-loss reporter, shadow/canary promotion gates, flip `shadow_mode=false`, SWE-bench pilot.
- **Phase 5b — learning-loop evaluation:** `learning_eval.py`, chronological replay, genesis/no-learning baseline, calibration-improvement report.
- **Phase 6 — v1.5:** Monte Carlo (numpy), Ed25519 signing (cryptography), multi-language graphs, `pebra_explain`.

---

## 15. Success Criteria (v1)

- `pebra assess` produces a correct, human-readable risk card from a JSON request with **zero pip deps**.
- The decision math reproduces the spec worked example exactly (see Appendix A).
- `core/` imports nothing outside stdlib (enforced by a test that `ast.walk`s for forbidden imports).
- Every decision is logged with a verifiable hash chain; outcomes can be recorded; the calibration view populates.
- Learned risk facts and active snapshots are stored append-only in SQLite, with every assessment pinned to the snapshot it used.
- The next assessment reapplies promoted learned facts through `apply_snapshot` before scoring, with deterministic scope precedence.
- Stale learned facts decay by effective weight based on scoped churn, without deleting ledger history.
- Snapshot promotion is backed by counterfactual replay and can be frozen by reconciliation drift.
- Swapping the blast-radius provider (ast ↔ sem) requires no change to `core/`.
- Post-assessment guardrails detect stale evidence and scope drift before commit/PR.

---

## Appendix A — Worked Example Verification (spec §10.2, recomputed from the formulas)

Action `a1` (Patch validate_login, **C3**, domain auth):
- events with event-class-aware floor (C3 floor 0.80): `test_regression` p0.10×0.40=0.04 (not floored); `public_api_break` p0.03×0.80=0.024; `security_sensitive_change` p0.04×0.90=0.036 → **expected_loss = 0.10** ✓
- **EU** = 0.74·0.82 − 0.10 − 0.12 = **0.3868 ≈ 0.39** ✓
- **utility_sd** = √0.0036 = **0.06** ✓ (from `DEFAULT_VARIANCE`, AD-5)
- **RAU** = 0.3868 − 1.28·0.06 = **0.3100 ≈ 0.31** → band "proceedable" ✓
- **edit_confidence** = exp(mean(ln[0.74,0.78,0.80,0.92,0.86,0.92])) = **0.8338 ≈ 0.83** → "high" ✓
- **risk_budget_used** = 0.10 / 0.20 (C3 effective threshold) = **50%** ✓
- **Decision:** RAU>0, loss under limit, confidence high → **proceed**; C3 → `requires_confirmation=true` ✓

## Appendix B — `.pebra.yml` (annotated, abbreviated)
See spec §12 for the full key list. Notable: `criticality` globs use C-stages (`src/payments/**: C4`); `thresholds` include `c3_max_expected_loss_without_human: 0.20`, `c4_always_ask_human: true`, `high/low_edit_confidence`; `edit_confidence_weights` parsed as `N/M` fractions; `medium_auto_proceed_requires` is `# v1.5 reserved` (AD-6).

## Appendix C — Spec Cross-Reference
Architecture §2 → layer diagram; §3 → authoritative module table; §4 → constants/glossary; §5 → score math and blast evidence contract; §6 → decision engine; §7 → assessment dataclasses; §8 → human/JSON output; §9 → post-assessment guardrails; §10 → SQLite store, audit, and learned memory; §11 → reference attributions; §12 → dependency tiers; §13 → architecture decisions; §14 → build sequence.
