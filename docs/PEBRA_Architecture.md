# PEBRA Architecture

*Companion to `PEBRA_Report_Final.md` (the spec). This document maps **how to build** PEBRA: the layering, the module placement, the resolved design decisions, the storage, and the build order. Section references like §7.1 point to the spec.*

*Status: design — for review. Nothing here is implemented yet.*

---

## 1. Purpose

The spec defines **what** PEBRA computes (benefit-risk scores → a 5-way decision). This document defines **how** to implement it: a lean, stdlib-first, hexagonal Python tool whose **pure decision core** is dependency-free and auditable, with all messy I/O pushed to adapters.

It also records **10 Architecture Decisions (AD-1…AD-10)** that resolve gaps the spec left open, and the **human-facing label glossary** so the output is readable by people, not just agents.

**How to read it:** §2–§3 = structure; §4–§9 = the contracts (constants, math, gates, output, store); §10–§11 = what we borrow and what we depend on; §12 = the resolved decisions; §13 = build order.

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
        |  Store · OutcomePort · CalibrationPort      |
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
| constants / enums | core | `core/constants.py` | — | — |
| EvidenceProvider | ports | `ports/evidence_port.py` | `typing.Protocol` | — |
| BlastRadiusProvider | ports | `ports/blast_radius_port.py` | Protocol | — |
| OutcomePort | ports | `ports/outcome_port.py` | Protocol | — |
| CalibrationPort | ports | `ports/calibration_port.py` | Protocol | — |
| ast import graph (default blast) | adapters | `adapters/ast_import_graph.py` | **codeindex** `impact.py` (`d+0.5·t`) | Apache-2.0 |
| sem adapter (optional) | adapters | `adapters/sem_adapter.py` | subprocess | MIT/Apache-2.0 |
| codeindex adapter (optional) | adapters | `adapters/codeindex_adapter.py` | subprocess | Apache-2.0 |
| radon adapter (optional) | adapters | `adapters/radon_adapter.py` | radon | MIT |
| bandit adapter (optional) | adapters | `adapters/bandit_adapter.py` | bandit | Apache-2.0 |
| git adapter | adapters | `adapters/git_adapter.py` | subprocess git | — |
| yaml config | adapters | `adapters/yaml_config.py` | pyyaml (opt) / stdlib | MIT |
| sqlite store | adapters | `adapters/store/db.py` | **codeindex** `store/db.py` + **Aegis** hash-chain idiom | Apache-2.0 / pattern |
| outcome logger | adapters | `adapters/outcome_logger.py` | sqlite store | — |
| calibration store | adapters | `adapters/calibration_store.py` | sqlite store | — |
| `pebra assess` / `record-outcome` | cli | `cli/*.py` | stdlib argparse | — |
| `pebra_compare` / `pebra_assess` / `pebra_record_outcome` | mcp_server | `mcp_server/server.py` | MCP stdio (codeindex pattern) | — |

---

## 4. Canonical Constants & Vocabulary  (`core/constants.py`)

**Decision enum (exactly 5):** `proceed · inspect_first · test_first · ask_human · reject`. Companion fields (NOT decisions): `requires_confirmation: bool`, `action_status: pending|completed|skipped|rejected`.

**STAGE_MAP** (spec §2.7) — ordinal stage → cardinal value; the raw stage is never multiplied, only mapped:
`C0→0.10 · C1→0.30 · C2→0.50 · C3→0.80 · C4→1.00`

**CONSEQUENCE_BEARING_EVENTS** (see AD-1): `{public_api_break, security_sensitive_change, external_state_damage, migration_failure, dependency_break}`. The criticality floor applies **only** to these.

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

---

## 6. Decision Gate Sequence  (`core/decision_engine.py` — §8 is the SOLE authority)

Ordered; first match wins:

1. policy violation → **reject**
2. `criticality_stage == C4` and `c4_always_ask_human` → **ask_human** (`requires_confirmation=true`)
3. `expected_loss > effective_threshold` → **ask_human** (or **reject** if `expected_utility < 0`)
4. **`RAU < 0` → reject** (default) / ask_human if `ask_on_negative_rau` configured  *(AD-2 — **PROPOSED** gate, pending spec §8.2 ratification; implement behind a comment referencing AD-2, do not treat as confirmed spec)*
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

## 9. SQLite Store  (`adapters/store/db.py`)

WAL mode, foreign keys on, schema versioned. **JSON-as-truth:** the full request/response blobs are the source of truth; relational columns are projections (rebuildable). `PRAGMA integrity_check` on open → delete+rebuild cache if corrupt.

```sql
assessments(id PK, task, schema_version, request_json, response_json,
            recommended_decision, created_at,
            previous_hash, integrity_hash,        -- SHA-256 chain (stdlib hashlib)
            shadow_mode INTEGER DEFAULT 1)         -- day-one logging; excluded from calibration
outcomes(id PK, assessment_id FK, action_id, terminal_status, actual_result,
         recorded_at, previous_hash, integrity_hash)
criticality_cache(path_pattern PK, criticality_stage, source_type, cached_at)
VIEW calibration_data AS  -- joins assessments+outcomes WHERE shadow_mode=0 → the training set
```

**Hash-chain rule** (idiom from Aegis, reimplemented in stdlib `hashlib` — no code copied):
For **assessments**: `integrity_hash = sha256(canonical({id, created_at, recommended_decision, sha256(request_json), sha256(response_json), previous_hash}))` — it covers `sha256(response_json)`, the canonical response that is the source of truth, not just the request. For **outcomes**: `integrity_hash = sha256(canonical({id, assessment_id, action_id, terminal_status, actual_result, recorded_at, previous_hash}))`. `previous_hash` = prior row's `integrity_hash`. `validate_chain()` re-walks rows and recomputes. Tamper-evident with zero deps.

**Shadow mode** is the cold-start answer: v1 logs every assessment with `shadow_mode=1`; the agent/human proceeds normally; outcomes accumulate; when there's enough data, flip to `shadow_mode=0` and the `calibration_data` view feeds v1.5 calibration — **no re-architecting.**

---

## 10. Reference Patterns & Attributions

| Pattern | Source | License | Use |
|---|---|---|---|
| SQLite WAL store, schema versioning, soft-delete | codeindex `store/db.py` | Apache-2.0 | adapt pattern (Apache-2.0 permits direct reuse with attribution + NOTICE if we choose to copy) |
| Blast-score `d + 0.5·transitive`, BFS over reverse import graph | codeindex `impact.py:62` | Apache-2.0 | adapt pattern / implement compatible adapter |
| MCP stdio JSON-RPC (`TOOLS` list + `_HANDLERS` dict + `serve()`) | codeindex `mcp_server.py` | Apache-2.0 | adapt pattern |
| Per-language analyzer registry | codeindex `analyze.py` | Apache-2.0 | adapt pattern |
| `gate_check` UX: verdict + `reasons[]`, human-first / JSON-under, cycle detection as hard signal | code-impact-mcp | MIT | adapt UX pattern (upgrade 3-way → 5-way) |
| Hexagonal core/ports/adapters, fail-open hook, `_meta` envelope | Ctxo | MIT | adapt pattern (reimplement in Python) |
| SHA-256 `previous_hash`/`integrity_hash` chain idiom | Aegis | **pattern only** | reimplement in stdlib `hashlib`; *verify Aegis license before copying any code* |
| MCDA math (CRITIC/Entropy/ROC/BWM) | pyDecision | **GPL-3** | **reference only** — clean-room from published formulas, never import |
| Blast-radius provider (multi-lang) | sem (external binary) | MIT/Apache-2.0 | consume via subprocess |

**Correction (important):** **Aegis is a remote gateway / transport, not a local store.** Do not cite it for the store pattern — that comes from codeindex/Ctxo. Aegis contributes **only** the hash-chain integrity idiom, which we reimplement with stdlib.

---

## 11. Dependency Tiers

- **v1 core — zero pip deps (stdlib only):** `ast, sqlite3, hashlib, json, dataclasses, typing, math, subprocess, pathlib, argparse, logging, fnmatch, uuid, tomllib`. Target (not yet implemented; to verify at Phase 0): `pebra assess <file>` runs and prints a risk card with **no `pip install`**.
- **v1 optional extras (graceful fallback if absent):** `radon` (complexity), `bandit` (SAST), `pyyaml` (`.pebra.yml`), `sem`/`codeindex` (external binaries → richer blast radius). Missing tool → adapter returns `None` → falls back to built-in AST blast.
- **v1.5 extras:** `scikit-learn`+`numpy` (calibration, Monte Carlo), `cryptography` (optional Ed25519 signing), `mapie` (conformal intervals).
- **Hard rule:** no GPL/AGPL runtime deps, ever. pyDecision stays reference-only.

---

## 12. Architecture Decisions (resolved)

- **AD-1 — Event-class-aware criticality floor.** Define `CONSEQUENCE_BEARING_EVENTS` in `constants.py`; the floor `max(elicited, STAGE_MAP[stage])` applies only to those events. `test_regression`/`review_burden` keep their elicited disutility. *Reproduces the worked example: `test_regression` stays 0.40, `expected_loss=0.10`, `EU=0.39`.* Enforced in `score_math._apply_floor`. **(Spec updated — §5.5 now states the event-class-aware floor.)**
- **AD-2 — `RAU < 0` is a formal gate** (not just a band), placed at gate 4 → `reject` by default (`ask_human` if configured). Enforced in `decision_engine`. **(Proposed addition to spec §8.2 gate sequence.)**
- **AD-3 — SD gate keeps `expected_utility > 0`.** The EU<0 case is already covered by gates 3/4, so the SD gate (gate 5) handles only the "positive mean, wide downside" case. Not a bug; documented in code.
- **AD-4 — `action_status` ownership:** `assessment_builder` writes `pending`; `outcome_logger` writes terminal states via `OutcomePort`. Nothing else writes it.
- **AD-5 — `utility_sd` inputs:** map each score's `confidence` to variance via `variance = ((1−confidence)/2)²`; cold-start uses `DEFAULT_VARIANCE` in `constants.py` (anchored so the worked example yields `SD=0.06`). **(Proposed addition to spec §7.2.)**
- **AD-6 — `medium_auto_proceed_requires` dropped from v1** (its flags `targeted_checks_pass`/`residual_blast_radius_low`/`no_policy_violation` were undefined). The medium-band re-score + existing gate sequence already cover the intent. Key marked `# v1.5 reserved`; loader warns if present. **(Spec updated — §12 marks the key v1.5-reserved.)**
- **AD-7 — `requires_confirmation` is proceed-only.** Meaningful (and shown) only with `proceed`; set `false` and omitted from the card on every other decision (you can't "confirm" an `ask_human`).
- **AD-8 — One canonical request schema.** `pebra_compare` takes the full §3.1 multi-action request; `pebra_assess` is a single-action short form that builds the same `AssessmentRequest`. No second schema.
- **AD-9 — Cold-start defaults** (`COLD_START_PRIORS`, `COLD_START_P_SUCCESS`, `COLD_START_BENEFIT`, `COLD_START_DISUTILITY`) in `constants.py`, keyed by action class, all tagged `prior_uncalibrated`. Anchored to the spec worked example so a fresh install produces sane numbers without elicitation.
- **AD-10 — Hexagonal layer assignment** is the §3 table (core / ports / adapters / cli / mcp_server), authoritative.

---

## 13. Build Sequence

- **Phase 0 — zero-dep skeleton → first runnable milestone.** `constants.py`, `score_math.py`, `request_validator.py`, `candidate_parser.py`, `assessment_builder.py`, `decision_engine.py`, `explanation_generator.py`, `ports/*`, `adapters/ast_import_graph.py`, `adapters/git_adapter.py`, `adapters/store/db.py`, `cli/assess.py`.
  **Milestone:** `python -m pebra assess examples/login_patch.json` prints the human card — **zero pip installs.**
- **Phase 1 — config + evidence quality:** `yaml_config`, `radon`/`bandit` adapters (optional), `confidence_gate`, `weight_resolver`, `query_validator`.
- **Phase 2 — MCP + outcomes:** `mcp_server` (`pebra_compare`/`pebra_assess`/`pebra_record_outcome`), `outcome_logger`, `calibration_store` (shadow read), `cli/record_outcome`.
- **Phase 3 — optional tool adapters:** `sem`, `codeindex`; evidence registry fallback order codeindex → sem → ast.
- **Phase 4 — calibration loop:** read `calibration_data`, Brier reporter, flip `shadow_mode=false`, SWE-bench pilot.
- **Phase 5 — v1.5:** Monte Carlo (numpy), Ed25519 signing (cryptography), multi-language graphs, `pebra_explain`.

---

## 14. Success Criteria (v1)

- `pebra assess` produces a correct, human-readable risk card from a JSON request with **zero pip deps**.
- The decision math reproduces the spec worked example exactly (see Appendix A).
- `core/` imports nothing outside stdlib (enforced by a test that `ast.walk`s for forbidden imports).
- Every decision is logged with a verifiable hash chain; outcomes can be recorded; the calibration view populates.
- Swapping the blast-radius provider (ast ↔ sem) requires no change to `core/`.

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
§2 → constants/glossary; §3 → request_validator; §4 → evidence adapters; §5/§7 → score_math; §6 → weight_resolver; §8 → decision_engine; §9 → explanation_generator; §11 → module table; §12 → yaml_config; §13 → build sequence.
