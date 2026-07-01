# PEBRA Architecture

*Companion to `PEBRA_Report_Final.md` (the spec). This document maps **how to build** PEBRA: the layering, the module placement, the resolved design decisions, the storage, and the build order. Section references like §7.1 point to the spec.*

*Status: design — for review. Nothing here is implemented yet.*

---

## 1. Purpose

The spec defines **what** PEBRA computes (benefit-risk scores → a 5-way decision). This document defines **how** to implement it: a lean, stdlib-first, hexagonal Python tool whose **pure decision core** is dependency-free and auditable, with all messy I/O pushed to adapters.

It also records **29 Architecture Decisions (AD-1…AD-29)** that resolve gaps the spec left open, and the **human-facing label glossary** so the output is readable by people, not just agents.

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
        |                  app/                       |   (use-case controllers)
        | assess · verify · record_outcome ·          |
        | accept_risk · run_learning                  |
        +----------------+----------------+-----------+
                 |                         |
                 v                         v
        +---------------------------------------------+
        |                  ports/                     |   (Protocol interfaces)
        |  EvidenceProvider · BlastRadiusProvider     |
        |  ArchitectureKnowledgeProvider              |
        |  SymbolDiffProvider                         |
        |  ChangeVerifier · ContractSurfaceProvider   |
        |  StorePort · OutcomePort · CalibrationPort  |
        |  LearningPort · RepositoryRegistryPort      |
        +----------------+----------------+-----------+
                 ^                         |
   implemented by|                         | called by
                 |                         v
        +--------+---------+   +-----------------------------+
        |    adapters/     |   |           core/             |
        |  codegraph       |   |  PURE DOMAIN, stdlib-only   |
        |  radon · bandit  |   |  scoring math · gates ·     |
        |  git · yaml      |   |  decision engine · explainer|
        |  sqlite_store    |   |  constants · models         |
        |  store readers   |   |  (NEVER imports adapters)   |
        +------------------+   +-----------------------------+
```

**The one rule that matters:** `core/` imports **only** pure stdlib + `core/`. It never imports `ports/`, `adapters/`, `app/`, `cli/`, `mcp_server/`, `dashboard/`, any pip package, or I/O-oriented stdlib modules such as `sqlite3`, `subprocess`, or `argparse`. `app/` owns use-case orchestration and imports only `core/` + `ports/`; surfaces compose concrete adapters and pass them through ports. Adapters depend on `core/` and `ports/`; `core/` depends on nothing outside the deterministic standard-library subset. This is what keeps the decision brain deterministic, testable in isolation, and auditable.

**Why hexagonal:** the value of PEBRA is the *decision math*, which must be reproducible and explainable. Isolating it from I/O (graphs, git, SQLite, MCP) means every number is a pure function of its inputs — an auditor can reconstruct any decision from `core/` alone. CodeGraph is the required precision graph engine, but it remains an adapter-side evidence source: PEBRA owns the normalized graph schema, fan-in percentile math, edge-confidence policy, risk/benefit scoring, learning, audit chain, and fallback/fail-closed behavior.

---

## 3. Module → Layer Canonical Table

| Module | Layer | File | Borrows from | License |
|---|---|---|---|---|
| request validator | core | `core/request_validator.py` | stdlib | — |
| candidate parser | core | `core/candidate_parser.py` | stdlib | — |
| query validator | core | `core/query_validator.py` | stdlib | — |
| assessment builder | core | `core/assessment_builder.py` | stdlib | — |
| score math | core | `core/score_math.py` | stdlib `math` | — |
| benefit model | core | `core/benefit_model.py` | stdlib `math` | AD-28 |
| score normalizer | core | `core/score_normalizer.py` | stdlib | — |
| weight resolver | core | `core/weight_resolver.py` | stdlib | — |
| confidence gate | core | `core/confidence_gate.py` | stdlib | — |
| decision engine | core | `core/decision_engine.py` | stdlib | — |
| explanation generator | core | `core/explanation_generator.py` | stdlib | — |
| model guidance renderer | core | `core/model_guidance.py` | stdlib | — |
| change classifier | core | `core/change_classifier.py` | stdlib | — |
| high-risk control selector | core | `core/high_risk_controls.py` | stdlib | — |
| data models | core | `core/models.py` | stdlib dataclasses / typing | — |
| prediction error scorer | core | `core/prediction_error.py` | stdlib `math` | — |
| risk learning measurement | app/core | `app/learning_controller.py` + `core/prediction_error.py` + `core/outcome_labels.py` | stdlib | — |
| snapshot/fact resolver | core | `core/apply_snapshot.py` (`_candidates` / `_winner` / `_resolve_target`) | stdlib `fnmatch` | — |
| snapshot reapplication | core | `core/apply_snapshot.py` | stdlib `fnmatch` | — |
| top-k fact composer | core | `core/topk_composer.py` | stdlib `math` | — |
| scope DAG resolver | core | `core/scope_dag_resolver.py` | stdlib | — |
| risk fact decay | core | `core/risk_fact_decay.py` | stdlib `math` | — |
| promotion evaluator | core | `core/promotion_evaluator.py` | stdlib | — |
| snapshot reconciler | core | `core/snapshot_reconciler.py` | stdlib | — |
| learning evaluator | core | `core/learning_eval.py` | stdlib | — |
| contradiction gate | core | `core/contradiction_gate.py` | stdlib | — |
| constants / enums | core | `core/constants.py` | — | — |
| EvidenceProvider | ports | `ports/evidence_port.py` | `typing.Protocol` | — |
| BlastRadiusProvider | ports | `ports/blast_radius_port.py` | Protocol | — |
| ArchitectureKnowledgeProvider | ports | `ports/architecture_knowledge_port.py` | Protocol | — |
| SymbolDiffProvider | ports | `ports/symbol_diff_port.py` | Protocol | — |
| ChangeVerifier | ports | `ports/change_verifier_port.py` | Protocol | — |
| ContractSurfaceProvider | ports | `ports/contract_surface_port.py` | Protocol | — |
| StorePort | ports | `ports/store_port.py` | Protocol | — |
| OutcomePort | ports | `ports/outcome_port.py` | Protocol | — |
| CalibrationPort | ports | `ports/calibration_port.py` | Protocol | — |
| LearningPort | ports | `ports/learning_port.py` | Protocol | — |
| SanctionPort | ports | `ports/sanction_port.py` | Protocol | — |
| RepositoryRegistryPort | ports | `ports/repository_registry_port.py` | Protocol | — |
| CodeGraphProvider | ports | `ports/codegraph_provider_port.py` | Protocol | — |
| post-assessment guardrails | core | `core/post_assessment_guardrails.py` | stdlib | — |
| repo/path resolver | adapters | `adapters/paths.py` | codeindex/Ctxo walk-up pattern | Apache-2.0 / MIT reference |
| CodeGraph adapter | adapters | `adapters/codegraph_adapter.py` | required CodeGraph CLI + read-only SQLite DB | MIT |
| AST diff adapter | adapters | `adapters/ast_diff_adapter.py` | git diff + CodeGraph symbol map | — / MIT evidence |
| graph blast adapter | adapters | `adapters/ast_import_graph.py` or successor `adapters/codegraph_blast.py` | CodeGraph reverse-edge graph + PEBRA math | MIT evidence |
| architecture map adapter | adapters | `adapters/architecture_map.py` | CodeGraph nodes/edges + PEBRA summaries | MIT evidence |
| git change verifier | adapters | `adapters/git_change_verifier.py` | GitNexus `detect_changes` concept | PolyForm NC reference-only |
| contract surface scanner | adapters | `adapters/contract_surface.py` | GitNexus `api_impact`/`route_map`/`tool_map`/`shape_check` concepts | PolyForm NC reference-only |
| radon adapter | adapters | `adapters/radon_adapter.py` | radon | MIT |
| bandit adapter | adapters | `adapters/bandit_adapter.py` | bandit | Apache-2.0 |
| git adapter | adapters | `adapters/git_adapter.py` | subprocess git | — |
| yaml config | adapters | `adapters/yaml_config.py` | pyyaml | MIT |
| sqlite store | adapters | `adapters/store/db.py` | **codeindex** `store/db.py` + **Aegis** hash-chain idiom | Apache-2.0 / pattern |
| repository registry | adapters | `adapters/repository_registry.py` | agentmemory config pattern + local registry | Apache-2.0 reference |
| outcome logger | adapters | `adapters/outcome_logger.py` | sqlite store | — |
| calibration store | adapters | `adapters/calibration_store.py` | sqlite store | — |
| learning store | adapters | `adapters/learning_store.py` | sqlite store | — |
| sanction store | adapters | `adapters/sanction_store.py` | sqlite store | — |
| flow benchmark replay | benchmarks | `benchmarks/flow/replay.py` | PEBRA pipeline | — |
| flow benchmark scorecard | benchmarks | `benchmarks/flow/scorecard.py` | pandas / scipy / plots | permissive |
| benchmark structural adapters | benchmarks | `benchmarks/flow/adapters/structural/*.py` | codeindex; GitNexus external comparator | Apache-2.0 / external only |
| benchmark JIT loaders | benchmarks | `benchmarks/flow/adapters/jit/*.py` | ApacheJIT / JIT-Defects4J datasets | dataset terms |
| benchmark agent runner | benchmarks | `benchmarks/flow/adapters/agent/*.py` | SWE-bench-style runner | benchmark-only |
| Risk Observatory server | dashboard | `dashboard/server.py` | agentmemory viewer pattern | Apache-2.0 reference |
| dashboard port allocator | dashboard | `dashboard/ports.py` | agentmemory `--port` / `--instance` pattern | Apache-2.0 reference |
| Risk Observatory renderer | dashboard | `dashboard/templates/*.html` / `dashboard/static/*` | vanilla local UI | permissive |
| Risk Observatory API | dashboard | `dashboard/api.py` | store + scorecard readers | — |
| assess controller | app | `app/assess_controller.py` | use-case orchestration | — |
| verify controller | app | `app/verify_controller.py` | use-case orchestration | — |
| record-outcome controller | app | `app/record_outcome_controller.py` | use-case orchestration | — |
| accept-risk controller | app | `app/accept_risk_controller.py` | sanction workflow | — |
| learning controller | app | `app/learning_controller.py` | batch learning orchestration | — |
| `pebra assess` / `verify` / `accept-risk` / `record-outcome` | cli | `cli/*.py` | stdlib argparse | — |
| `pebra_compare` / `pebra_assess` / `pebra_verify` / `pebra_accept_risk` / `pebra_record_outcome` | mcp_server | `mcp_server/server.py` | MCP stdio (codeindex pattern) | — |

---

## 4. Canonical Constants & Vocabulary  (`core/constants.py`)

**Decision enum (exactly 5):** `proceed · inspect_first · test_first · ask_human · reject`. Companion fields (NOT decisions): `requires_confirmation: bool`, `action_status: pending|completed|skipped|rejected`.

**STAGE_MAP** (spec §2.7) — ordinal stage → cardinal value; the raw stage is never multiplied, only mapped:
`C0→0.10 · C1→0.30 · C2→0.50 · C3→0.80 · C4→1.00`

**CONSEQUENCE_BEARING_EVENTS** (see AD-1): `{public_api_break, security_sensitive_change, external_state_damage, migration_failure, dependency_break, api_contract_break, route_behavior_break, tool_schema_break, response_shape_mismatch, consumer_shape_mismatch}`. The criticality floor applies **only** to these.

**LOG_LOSS_CLIP_EPS:** `1e-15`. Log-loss scoring clips probabilities to `[LOG_LOSS_CLIP_EPS, 1 - LOG_LOSS_CLIP_EPS]` so confident-wrong predictions remain finite and deterministic.

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
benefit         = resolve_benefit(benefit_breakdown)                  # §5.1 / AD-28
expected_utility= p_success · benefit − expected_loss − review_cost   # §7.1
utility_sd      = sqrt( benefit²·Var(p_success) + p_success²·Var(benefit)
                        + Var(review_cost) + Σ event_variance + scenario_variance )  # §7.2, first-order
RAU             = expected_utility − 1.28 · utility_sd                # §7.1 (z=1.28, 90% lower bound)
net_benefit_score = benefit − expected_loss − review_cost             # §12.14, alternative ranking lens
edit_confidence = exp( Σ_i w_i · ln(x_i) )   over 6 factors, w_i=1/6  # §7.4 weighted geometric mean
risk_budget_used= expected_loss / effective_threshold                # ratio; >1.0 = over budget
blast_score     = direct + 0.5 · transitive   (normalized to [0,1])  # codeindex impact.py:62
```

Every output is wrapped with provenance (`source_type=derived, provider=pebra, formula=…`). No randomness, no model calls in v1 → fully reconstructable.

**BenefitModel output contract** (`core/benefit_model.py`, AD-28):

```text
BenefitBreakdown {
  immediate_benefit,
  future_maintenance_savings,
  technical_debt_interest,
  recurrence_risk,
  expected_rework_cost,
  information_value,
  strategic_business_value,
  benefit,
  benefit_variance,
  net_benefit_score,
  component_provenance[]
}

BenefitDeltaEvidence {
  scope,
  before_ref,
  after_ref,
  source_type: derived | measured | projected,
  deltas: {
    complexity_delta,
    modularity_delta,
    coupling_delta,
    cohesion_delta,
    testability_delta,
    analyzability_delta,
    modifiability_delta,
    duplication_delta,
    encapsulation_delta,
    api_surface_delta,
    reusability_delta,
    portability_delta,
    observability_delta,
    operability_delta,
    recurrence_delta
  },
  future_change_exposure,
  maintenance_effort_delta_per_change
}
```

`core/benefit_model.py` receives already-collected metrics and proposed/actual diff evidence. It does not read files, call git, run radon, inspect coverage, or query issue trackers. Adapters provide before/after metrics; the core computes directionality, normalization, exposure weighting, `benefit`, and `Var(benefit)`.

Directionality is explicit per metric: lower is better for complexity, coupling, duplication, public surface, hidden side effects, debt interest, and recurrence; higher is better for testability, cohesion, modularity, encapsulation, observability, and operability. A new abstraction is not automatically beneficial; it earns positive value only when the measured deltas reduce future change effort.

Phase-0 benefit stub: when no concrete proposed patch metrics are available, the Phase-0 evidence path supplies `BenefitDeltaEvidence` with all deltas `0.0`, `source_type=projected`, and `future_change_exposure=0.0`. In that mode `benefit = immediate_benefit`, projected maintainability improvement receives no gate-driving credit, and `Var(benefit)` widens according to the cold-start/projected-component variance rule.

**WeightResolver contract** (`core/weight_resolver.py`): receives configured, elicited, or cold-start criterion weights and returns normalized weights with provenance. It does not read config files; adapters load config and pass values in. It must reject negative weights, handle missing weights by documented cold-start defaults, normalize to `sum(weights)=1`, and report consistency warnings without mutating the assessment.

**ScoreNormalizer contract** (`core/score_normalizer.py`): receives raw evidence metrics and metric metadata, then maps them to score ranges expected by `score_math` (`[0,1]`, `[-1,1]`, or named bands). It must be deterministic, monotonic for declared directions, and provenance-preserving. It may not call tools, read files, or infer new evidence.

**ConfidenceGate contract** (`core/confidence_gate.py`): receives edit-confidence factors, confidence-band thresholds, and evidence-delta metadata, then returns the confidence band plus any required evidence action. It does not make the final decision; `decision_engine` is the sole gate authority. It enforces retrieval-only caps and low-confidence-upgrade evidence requirements before the decision engine consumes the result.

**Canonical risk-resolution stack (AD-27):**

```text
Layer 0 raw evidence
  files, paths, criticality globs, blast graph, architecture anchors, tests, policy
Layer 1 symbol/scope resolution
  changed symbol, change_kind, visibility, fan-in percentile, side effects, fallback reason
Layer 2 scores
  p_event, p_success, expected_loss, risk_budget_used, RAU, confidence, review_cost
Layer 3 gates
  exactly five decisions: proceed / inspect_first / test_first / ask_human / reject
Layer 4 risk annotations
  risk_mode, high_risk_triggers[], trigger_summary, suppression reasons
Layer 5 controls
  required checks, controlled-high-risk blueprint, sanction requirements
```

Layer ordering is mandatory. File-level criticality, blast, and god-node status feed Layer 1 first when symbol evidence exists; they do not directly inflate formulas. Layer 4 never re-queries repo evidence and never influences Layer 3. It is a read-only rendering of the evidence, scores, and gates that already ran.

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

**ArchitectureKnowledgeProvider output contract** (derived codebase map, not decision memory):

```text
ArchitectureEvidence {
  graph_commit,
  graph_freshness: fresh | rebuilt | stale | unknown,
  matched_anchors[],
  matched_domains[],
  architecture_anchor_score,
  god_node_score,
  bridge_centrality,
  domain_entrypoint,
  fan_out,
  cycle_participation,
  domain_criticality_hint,
  source_files[]
}
```

This provider reads the required local CodeGraph index as PEBRA's precision code graph. CodeGraph owns multi-language extraction, cross-file resolution, file watching, and SQLite graph storage. PEBRA owns the deterministic interpretation of that graph: normalized feature names, symbol fan-in percentiles, edge-confidence tiering, affected-area math, criticality integration, risk/benefit scoring, learning, and audit provenance. PEBRA never ingests a CodeGraph "risk score"; it ingests graph facts.

The CodeGraph freshness contract is explicit. Before PEBRA trusts graph evidence, the adapter runs `codegraph sync --quiet <repo>` and then reads `codegraph status --json <repo>`. `fresh` means the status is initialized, has no pending added/modified/removed files, `index.reindexRecommended=false`, and no worktree mismatch. `rebuilt` means sync observed and repaired changes before the final clean status. `stale` means CodeGraph is unavailable, uninitialized, still reports pending changes after sync, recommends re-indexing, reports a worktree mismatch, or cannot be queried safely. `unknown` is reserved for non-repo or deliberately graphless fixture paths. A stale required graph is not silently treated as low risk; the evidence-validity gate routes would-be proceeds to `inspect_first` or fails closed for graph-required commands.

The canonical graph fields are CodeGraph `nodes`, `edges`, `files`, and `project_metadata`, normalized through PEBRA's `CodeGraphProvider` port. PEBRA records `codegraph_version`, `extraction_version`, graph freshness, and edge provenance in prediction provenance. Those version fields are calibration scope inputs: a CodeGraph extractor/version bump can change fan-in features and therefore must be visible to learning and benchmark scorecards.

A node like `SpreadsheetView.tsx` with very high fan-in and cross-domain edges becomes an architecture anchor / god-node signal, raising affected-area, review-cost, and `inspect_first` / `test_first` pressure. A lower-degree auth/account-linking node still becomes high risk through criticality/capability signals, not through centrality.

The split is intentional:

```text
architecture centrality / god-node status -> p_event, review_cost, Affected Area explanation
criticality / sensitive capability        -> disutility floor, tighter thresholds, confirmation
```

Graph derivation is repo-agnostic and CodeGraph-backed:

```text
architecture_nodes / architecture_edges <- CodeGraph nodes + edges
symbol_fan_in_percentile                <- PEBRA percentile over CodeGraph reverse edges
god_node_score                          <- repo-relative fan-in percentile, floored below anchor minimum
architecture anchors                    <- in-degree >= floor AND top fan-in percentile
bridge_centrality                       <- cross-directory / cross-package edge proxy
edge_confidence                         <- PEBRA mapping over CodeGraph edge provenance
domain_entrypoint                       <- route/page/CLI/MCP/main/run/handle_* heuristics
fan_out                                 <- outgoing import count of edited files
cycle_participation                     <- edited file participates in an import-cycle SCC
matched_domains                         <- coarse top-level directory / package grouping
domain_criticality_hint                 <- capability/path tokens such as auth, login, payment, billing, session, crypto
```

Future graph evidence additions are tracked separately and are **not** part of the current
computation-bug fix: transitive symbol blast and repo-relative blast fraction. They should enter as
additional graph evidence fields interpreted by PEBRA's existing risk/benefit model, not as a raw
replacement graph risk score.

Blast/architecture graph uncertainty is an evidence-quality signal, not fake reach. Missing expected files, parse-failed expected files, unresolved internal imports, dynamic imports, wildcard imports, and repo-wide dynamic/wildcard import surfaces produce bounded `graph_uncertainty_score` and provenance lists for model guidance. That score lowers `evidence_quality` and therefore `edit_confidence`; it never inflates `direct_count`, `transitive_count`, blast radius, or expected loss. External/stdlib imports are tracked but not penalized.

**SymbolDiffProvider output contract** (canonical Layer 1 symbol/scope evidence):

```text
SymbolDiffEvidence {
  parsed_patch_available,
  changed_symbols[],
  max_change_kind,
  consequential_symbol_changed,
  consequence_reason[],
  symbol_fan_in_percentile,
  transitive_reaches_consequence_symbol,
  directive_comment_changed,
  fallback_reason
}

SymbolDiff {
  symbol_id,
  file_path,
  symbol_kind,
  visibility: private | internal | exported | public_api | unknown,
  change_kind: COSMETIC | DIRECTIVE | TEST_ONLY | BEHAVIORAL | CONTRACT | SIDE_EFFECT | UNKNOWN,
  signature_changed,
  return_shape_changed,
  body_changed,
  control_flow_changed,
  external_side_effect_changed,
  db_write_changed,
  payment_api_changed,
  migration_changed,
  callers_count,
  callers_percentile,
  edge_confidence
}
```

`adapters/ast_diff_adapter.py` owns I/O: read proposed patch or actual git diff, parse ASTs where supported, map hunks to symbols, and look up per-symbol fan-in/architecture data. `core/change_classifier.py` is pure: it receives parsed `SymbolDiff` rows plus thresholds/config and returns the change-kind summary.

Change-kind rules:

- `COSMETIC`: whitespace, formatting, ordinary comments, or ordinary docstrings with no executable/semantic effect.
- `DIRECTIVE`: comments/pragmas that change behavior, such as `# type: ignore`, `# noqa`, `// @ts-nocheck`, lint disables, build annotations, or framework directives.
- `TEST_ONLY`: test-only changes that do not mutate fixtures, snapshots, data generators, or production code paths in a way that changes behavior.
- `BEHAVIORAL`: function/method body logic changed, including small edits such as `amount * 100 -> amount * 1000`.
- `CONTRACT`: signature, return shape, exported/public API, route/tool/schema, response shape, or consumer-visible behavior changed.
- `SIDE_EFFECT`: payment call, DB write, migration, deletion, external-state write, idempotency/retry/transaction-boundary behavior changed.
- `UNKNOWN`: parser, patch, or fan-in evidence is unavailable; fall back conservatively to file/path-level risk.

Symbol/scope evidence is canonical assessment evidence, not only a high-risk filter. It feeds regular `p_event`, `blast_radius` / Affected Area, review cost, `risk_report` drivers, model guidance, high-risk triggers, `pebra_verify`, and learning buckets. C4 path, payment path, or god-node status alone is not enough to trigger controlled high-risk treatment when the classifier verifies `COSMETIC` or safe `TEST_ONLY`. The trigger requires a critical context **and** a consequential symbol/change:

```text
critical_context =
  criticality_stage in {C3,C4}
  OR god_node_score high
  OR domain_criticality_hint present

consequential_symbol_change =
  change_kind in {BEHAVIORAL, CONTRACT, SIDE_EFFECT, DIRECTIVE, UNKNOWN}
  AND (
    visibility in {exported, public_api}
    OR callers_percentile >= thresholds.consequential_symbol_fan_in_percentile
    OR transitive_reaches_consequence_symbol
    OR external_side_effect_changed
    OR db_write_changed
    OR payment_api_changed
    OR migration_changed
)
```

Absolute caller counts may be shown as explanation, but the high-risk trigger must be repo-relative (`callers_percentile`) or capability/side-effect based. This avoids magic thresholds such as "3 callers" that mean different things in a small repo and a monorepo. "Dead code" is not automatically cosmetic: only truly private, non-exported, non-entrypoint symbols with no dynamic-dispatch/reflection evidence and no transitive path to consequential symbols may be treated as low consequence. Otherwise use `UNKNOWN` or `BEHAVIORAL`.

`high_risk_triggers[]` is assembled in core after scoring/gates, not emitted by adapters. `SymbolDiffProvider` emits raw symbol evidence; `core/change_classifier.py` classifies it; `core/decision_engine.py` and `core/high_risk_controls.py` derive final triggers from that classified evidence, scores, policy, learned facts, evidence gaps, and gate results. A trigger is not a sixth decision and not a control by itself; it explains why the decision engine selected `ask_human`, `reject`, or `risk_mode=controlled_high_risk`, and which control blueprint would be required to proceed. If a possible trigger is suppressed by verified `COSMETIC` or safe `TEST_ONLY` classification, the suppression must be logged with `suppressible=true` and a `suppress_reason`.

---

## 6. Decision Gate Sequence  (`core/decision_engine.py` — §8 is the SOLE authority)

Ordered; the first matching risk gate sets a provisional decision. Sanction resolution may then finalize a risk-threshold `ask_human` / `reject` into controlled-high-risk `proceed` if the sanction is valid and pre-edit authorization controls are satisfied:

1. policy violation → **reject** with `high_risk_triggers[]` if the violation is risk/control related
2. `criticality_stage == C4` and `c4_always_ask_human` and the symbol diff is consequential or unknown → **ask_human** (`requires_confirmation=true`) with a C4/consequence trigger. Verified `COSMETIC` / safe `TEST_ONLY` edits in C4 paths remain sensitive-context edits, not controlled-high-risk edits.
3. `expected_loss > effective_threshold` → **ask_human** (or **reject** if `expected_utility < 0`) with the threshold trigger that fired
4. **`RAU < 0` → ask_human** (default) / reject if `ask_on_negative_rau=false` configured  *(AD-2 — now a formal numbered gate in spec §8.2)*
5. not MC and `utility_sd > max_utility_sd_without_human` and `expected_utility > 0` → **ask_human**  *(AD-3: EU<0 already handled by gate 3/4)*
6. MC available and `P(utility<0) > max_p_negative_utility` → ask_human/reject *(v1.5)*
7. `decision_instability > threshold` → **inspect_first** / **test_first**
8. `edit_confidence < low_edit_confidence` → inspect_first / test_first / ask_human / reject
9. confidence-upgrade requested without `evidence_delta` → reject
10. authorized sanction resolution may convert a risk-threshold `ask_human` / `reject` from gates 2/3/4/6 into **proceed** with `risk_mode=controlled_high_risk`, `requires_confirmation=true`, and binding controls. It never overrides gate 1 policy violations unless a distinct policy-exception sanction type is ratified.
11. else → **proceed** (set `requires_confirmation=true` if C3 or C4)

Sanction controls are split by timing. `pre_edit_authorization_controls` must be satisfied before gate 10 can convert the provisional decision. `pre_commit_required_controls` are bound into the guidance packet and verified later by `pebra_verify`; missing or failed pre-commit controls invalidate the sanction before commit/outcome logging.

**Double-count guard:** `criticality_stage` feeds the disutility floor and threshold tightening **only** — never `p_event`. Symbol/scope evidence, blast radius, and usage feed `p_event`, Affected Area, and review cost. The raw C-stage is never multiplied.

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

High-risk routes must not be emitted as bare `ask_human` or bare `reject`. When a decision or `risk_mode` is caused by high-risk conditions, canonical JSON includes:

```json
{
  "risk_mode": "controlled_high_risk",
  "high_risk_triggers": [
    {
      "trigger_id": "hrt_001",
      "risk_class": "payment_side_effect",
      "trigger_source": "symbol_diff",
      "severity": "critical",
      "affected_scope": "src/payments/charge.py::charge_customer",
      "evidence": ["payment_api_changed=true", "change_kind=SIDE_EFFECT", "criticality_stage=C4"],
      "decision_effect": "requires_controlled_high_risk_mode",
      "control_blueprint_id": "payment_change",
      "required_controls": ["sandbox_payment_tests", "idempotency_evidence", "reconciliation_baseline"],
      "suppressible": false,
      "suppress_reason": null,
      "provenance": {"provider": "pebra", "source_type": "derived"}
    }
  ],
  "trigger_summary": "Controlled high-risk mode triggered by a payment side-effect change to a consequential symbol."
}
```

Allowed `risk_mode` values: `normal`, `sensitive_context`, `elevated_review`, `controlled_high_risk`. `risk_mode` is a companion field like `requires_confirmation`, not a decision enum member. If `risk_mode=controlled_high_risk`, `high_risk_triggers[]` is non-empty.

Regular risk reports also carry `symbol_scope_evidence` with `scope_basis`, changed symbols, max change kind, visibility, fan-in percentile, consequence reason, and fallback reason. That block is required even when no high-risk trigger fires, so ordinary risk cards distinguish a cosmetic edit in a C4/god-node/payment file from a behavioral or contract change to a consequential symbol.

**Model guidance packet (agent-facing envelope):** PEBRA also renders a deterministic packet for the editing model. It is not a second reasoning system and it is not authored by an LLM. It is a pure rendering of the approved action envelope, the selected decision, and the explanation/gate outputs.

```json
{
  "guidance_packet_id": "gp_123",
  "decision": "test_first",
  "risk_mode": "elevated_review",
  "binding": {
    "safe_scope": {
      "files": ["src/components/data/SpreadsheetView.tsx", "src/components/data/__tests__/**"],
      "edit_policy": "targeted_patch_only"
    },
    "risky_scope": [
      {"change": "public API changes", "action": "requires_reassessment"},
      {"change": "dependency upgrades", "action": "requires_reassessment"},
      {"change": "schema changes", "action": "requires_reassessment"}
    ],
    "required_checks_before_commit": ["npm run test -- src/components/data/__tests__/SpreadsheetView"],
    "required_controls": []
  },
  "advisory": {
    "high_risk_triggers": [
      {
        "trigger_id": "hrt_001",
        "risk_class": "architecture_anchor_behavioral_change",
        "severity": "high",
        "decision_effect": "test_first",
        "control_blueprint_id": "broad_god_node_behavioral_edit"
      }
    ],
    "risk_facts": {
      "risk_level": "high",
      "affected_area": "high: architecture anchor / god node",
      "confidence": "medium"
    },
    "why": [
      "Touched code is an architecture anchor / god node with high affected area.",
      "A targeted patch plus tests keeps the edit inside the approved risk envelope."
    ],
    "suggested_inspection": ["inspect local call sites", "inspect formula/grid tests"],
    "safer_alternative": "make a targeted patch instead of refactoring the grid state model"
  },
  "provenance": {
    "safe_scope": "candidate action envelope",
    "risky_scope": "policy gates + detected risk events + architecture map + learned facts",
    "required_checks_before_commit": "test discovery + decision=test_first",
    "required_controls": "high_risk_triggers + control blueprint selector",
    "high_risk_triggers": "symbol_diff + criticality + gates + learned facts",
    "risk_facts": "risk_report + evidence discovery",
    "why": "explanation_generator"
  }
}
```

Binding fields are enforced later by `pebra_verify`; advisory fields steer the model but do not create new hard gates. This gives the model risk-aware editing context without letting the model reinterpret PEBRA's score. `risky_scope` is assessment-invalidating by default, not banned by default: touching a `requires_reassessment` item makes the prior risk score stale and forces a new assessment. Only `action: forbidden` is a hard rejection.

The packet may render trigger flags as prompt text, MCP fields, or PR-card rows, but the content must be reconstructable from canonical JSON. Trigger flags are advisory evidence; required controls and binding scope/check fields are what `pebra_verify` enforces.

---

## 9. Post-Assessment Guardrails  (`core/post_assessment_guardrails.py`)

This is the GitNexus-inspired priority-1 addition: PEBRA does not only approve an edit before it happens. In autonomy mode, PEBRA also verifies that the agent stayed inside the approved envelope before committing, opening a PR, or recording a successful outcome.

Hexagonal boundary: `core/post_assessment_guardrails.py` is pure. It receives already-fetched data such as the stored assessment, stored guidance packet, current diff summary, HEAD comparison, and contract-surface findings, then returns `GuardrailResult`. It must not call git, read the filesystem, import sqlite3, or inspect the repo directly. I/O belongs in adapters behind `ChangeVerifier`, `ContractSurfaceProvider`, `StorePort`, and the CLI/MCP entrypoints.

Priority order:

1. **Evidence freshness check** — compare the assessment's evidence commit to current `HEAD`. If `HEAD` changed, mark `evidence_freshness=stale` and require re-assessment (`inspect_first`) before autonomous proceed.
2. **Actual diff vs planned scope check** — compare the real git diff to the candidate action's `expected_files`, `affected_symbols`, dependency flags, and migration/schema flags. If the agent touched unexpected files, lockfiles, schemas, migrations, or broad call surfaces, set `scope_drift_detected=true` and route to `ask_human` or `reject`.
3. **Post-edit symbol reclassification** — rerun `SymbolDiffProvider` and `core/change_classifier.py` on the actual diff, not only on the proposed patch. Compare actual `max_change_kind`, changed symbols, and consequence flags against the stored pre-edit symbol diff. If the real diff is more severe than the pre-edit packet (for example, actual `BEHAVIORAL` or `SIDE_EFFECT` where pre-edit said `COSMETIC`), set `scope_drift_detected=true` and route to reassessment / `ask_human`.
4. **Contract surface change check** — detect public API, route handler, MCP/RPC tool schema, exported symbol, response-shape, and consumer-shape changes. These create event candidates such as `api_contract_break`, `route_behavior_break`, `tool_schema_break`, `response_shape_mismatch`, and `consumer_shape_mismatch`. This check is not a substitute for full symbol reclassification because body-logic changes may be dangerous without changing the surface.
5. **Dry-run refactor check** — for `rename`, `public_api_change`, `dependency_upgrade`, and `broad_refactor`, require an impact preview before editing. Without a preview, default to `inspect_first`.
6. **Pre-commit / PR risk card** — render the same human card at the commit/PR boundary, including any stale-evidence, scope-drift, symbol reclassification, or contract-surface findings.

Guardrail result shape:

```text
PostAssessmentGuardrails {
  evidence_freshness: fresh | stale | unknown,
  assessed_commit,
  current_head,
  scope_drift_detected,
  unexpected_files[],
  pre_edit_symbol_diff_summary,
  actual_symbol_diff_summary,
  symbol_change_mismatch,
  contract_surface_changes[],
  dry_run_required,
  pre_commit_decision,
  reasons[]
}
```

Decision integration: these checks run after an edit and before commit/PR. They do not replace the §6 decision engine; they feed it new evidence and may force a re-score. Hard failures map to existing decisions only: `inspect_first`, `test_first`, `ask_human`, or `reject`. There is no new decision enum.

Invocation:

```text
pebra verify --assessment-id <id> [--scope staged|all|branch] [--completed-check <id>=<status>]
pebra_verify({ assessment_id, scope, completed_checks[] })
```

`verify` loads the stored assessment, compares the current diff against `safe_scope`, `risky_scope`, and the other binding fields in the stored model guidance packet, runs the post-assessment guardrails, writes a `post_assessment_guardrails` row, and returns the same human card plus canonical JSON. `record-outcome` remains the terminal outcome logger; it should refuse a successful autonomous outcome if the latest `verify` result is stale or failing.

Binding enforcement map: `safe_scope` violations route to `inspect_first` / reassessment for narrow reviewable drift or `ask_human` / `reject` for broad unrelated drift; `risky_scope` entries with `action: requires_reassessment` route to `inspect_first` and invalidate the original assessment; `action: avoid_unless_required` routes to `ask_human` when touched without necessity evidence; `action: forbidden` routes to `reject`; missing `required_checks_before_commit` routes to `test_first`, while failed required checks route to `ask_human` unless a hard policy says `reject`.

This is the autonomy-envelope rule: **PEBRA may let an agent proceed on a branch, but only if the final diff still matches the approved risk envelope.** The model guidance packet is the pre-edit face of that envelope; `pebra_verify` is the post-edit enforcement.

---

## 10. SQLite Store  (`adapters/store/db.py`)

**Repo-scoped state is authoritative.** PEBRA is designed for developers with many local repos. The decision ledger, learning snapshots, outcome rows, architecture cache, and dashboard state are isolated per repo by default:

```text
<repo>/
  .pebra.yml                  # committed team policy
  .pebra/
    .gitignore                # auto-written; keeps local state out of git
    pebra.db                  # repo-local source of truth
    config                    # gitignored machine-local overrides
    dashboard.json            # last bound port, token metadata, pid, url
    architecture_cache/       # rebuildable derived artifacts
    scorecards/               # local project scorecards, not benchmark corpora
```

`adapters/paths.py` resolves the repo root by walking up from the caller's working directory: prefer an existing `.pebra/`, then the nearest `.git/` root. It returns `Path` values and repo metadata; it never imports `core/`. CLI, MCP, dashboard, and tests all use this adapter so the repo resolution rule is identical across surfaces.

**Machine registry is discovery, not truth.** A small registry lives outside repos (`%APPDATA%\pebra\registry.json` on Windows; `$XDG_STATE_HOME/pebra/registry.json` or `~/.local/state/pebra/registry.json` on Linux/macOS). It records known repos, display names, root paths, git remotes, last dashboard port, last seen time, and the repo-local DB path. It does **not** hold learned facts, risk snapshots, outcomes, or policy. Those stay in `<repo>/.pebra/pebra.db`. The registry exists so `pebra dashboard` can offer a repo switcher and so port reuse is predictable; deleting the registry must not lose PEBRA's risk history.

**Repo identity.** `repo_id` is derived from normalized git remote URL plus the resolved repo root. If no remote exists, use the resolved root path and initialize a stable local ID in `.pebra/config`. Every response includes `repo_id` and `repo_root` for auditability. Per-repo DBs are the default, so most tables do not need a `repo_id` column; the repo metadata is stored once in DB metadata and in the registry.

**Worktree isolation.** If `.git` is a file rather than a directory, PEBRA treats the checkout as a git worktree and warns before sharing parent-repo state. The default for agent/worktree use is to create a local `.pebra/` in that worktree so parallel branches do not mix assessments, learned facts, or active snapshots. A user may explicitly opt into sharing the parent repo's `.pebra/`, but the dashboard and CLI must label shared worktree state clearly.

**Configuration layering.** Runtime config is key-merged in this order: CLI flags / env vars -> `.pebra.yml` committed team policy -> `<repo>/.pebra/config` machine-local repo config -> machine registry/global config -> defaults. `.pebra.yml` owns team policy and risk thresholds; `.pebra/config` owns local paths, dashboard port preference, and machine-only settings. Policy/criticality changes should not be hidden in machine-local config.

**No cross-repo learning by default.** Learned facts, criticality suggestions, calibration parameters, guidance compliance, and active risk snapshots are repo-scoped. A future org/global prior may exist only as a weak cold-start prior and must never override a repo-specific learned fact or human-ratified project policy.

WAL mode, foreign keys on, schema versioned. **JSON-as-truth:** the full request/response blobs are the source of truth; relational columns are projections (rebuildable). `PRAGMA integrity_check` on open → delete+rebuild cache if corrupt.

```sql
assessments(id PK, task, schema_version, request_json, response_json,
            recommended_decision, created_at,
            assessed_commit, evidence_freshness,
            risk_snapshot_id, prediction_error_model_id,
            previous_hash, integrity_hash,        -- SHA-256 chain (stdlib hashlib)
            shadow_mode INTEGER DEFAULT 1)         -- day-one logging; excluded from calibration
outcomes(id PK, assessment_id FK, action_id, guidance_packet_id NULL FK,
         terminal_status, actual_result,
         recorded_at, previous_hash, integrity_hash)
post_assessment_guardrails(id PK, assessment_id FK, current_head,
                           scope_drift_detected, unexpected_files_json,
                           pre_edit_symbol_diff_json,
                           actual_symbol_diff_json,
                           symbol_change_mismatch,
                           contract_surface_changes_json, pre_commit_decision,
                           guidance_packet_id NULL FK,
                           safe_scope_status,
                           risky_scope_triggered_json,
                           risky_scope_actions_triggered_json,
                           completed_checks_json,
                           missing_checks_json,
                           failed_checks_json,
                           necessity_evidence_present,
                           reasons_json, recorded_at,
                           previous_hash, integrity_hash)
model_guidance_packets(guidance_packet_id PK, assessment_id FK, action_id,
                       decision, binding_json, advisory_json,
                       provenance_json,
                       calibration_scope CHECK(calibration_scope IN
                         ('proceeded_edits_only','guided_edit','shadow','canary','benchmark')),
                       created_at, previous_hash, integrity_hash)
sanction_events(sanction_id PK, assessment_id FK, action_id,
                repo_id, risk_snapshot_id, assessed_commit,
                guidance_packet_id NULL FK,
                sanction_type, sanction_scope, status,
                ratified_by, rationale,
                safe_scope_hash, risky_scope_hash,
                required_controls_hash, control_blueprint_id,
                risk_report_hash, expires_at,
                invalidated_reason, created_at,
                previous_hash, integrity_hash)
prediction_errors(prediction_id PK, assessment_id FK, action_id,
                  guidance_packet_id NULL FK,
                  risk_snapshot_id, prediction_error_model_id,
                  target, target_type, calibration_bucket,
                  predicted_probability, predicted_value,
                  actual_outcome, actual_value,
                  residual, brier_error, log_loss, squared_error,
                  benefit_guidance_influenced INTEGER,
                  outcome_label_status, calibration_scope,
                  recorded_at, previous_hash, integrity_hash)
learned_risk_facts(fact_id PK, fact_type, scope, statement,
                   scope_node_id,
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
architecture_nodes(node_id PK, label, source_file, node_type,
                   degree, bridge_degree, centrality_json,
                   domain_id, graph_commit, content_hash, active)
architecture_edges(edge_id PK, source_node_id, target_node_id,
                   edge_type, confidence, graph_commit, active)
architecture_anchors(anchor_id PK, node_id, source_file,
                     anchor_type, architecture_anchor_score,
                     god_node_score, bridge_centrality,
                     graph_commit, provenance_json)
architecture_domains(domain_id PK, name, anchor_node_id,
                     description, criticality_hint, source_file,
                     graph_commit, provenance_json)
VIEW calibration_data AS  -- observed prediction rows only; censored/counterfactual rows are excluded
  SELECT ...
  FROM prediction_errors
  JOIN assessments ON assessments.id = prediction_errors.assessment_id
  WHERE assessments.shadow_mode = 0
    AND prediction_errors.target_type = 'risk_binary'
    AND prediction_errors.outcome_label_status = 'observed'
    AND prediction_errors.calibration_scope = 'proceeded_edits_only'
    AND prediction_errors.guidance_packet_id IS NULL

VIEW benefit_binary_calibration_data AS
  SELECT ...
  FROM prediction_errors
  JOIN assessments ON assessments.id = prediction_errors.assessment_id
  WHERE assessments.shadow_mode = 0
    AND prediction_errors.target_type = 'benefit_binary'
    AND prediction_errors.outcome_label_status = 'observed'
    AND prediction_errors.calibration_scope = 'proceeded_edits_only'
    AND prediction_errors.guidance_packet_id IS NULL
    AND COALESCE(prediction_errors.benefit_guidance_influenced, 0) = 0

VIEW benefit_continuous_calibration_data AS
  SELECT ...
  FROM prediction_errors
  JOIN assessments ON assessments.id = prediction_errors.assessment_id
  WHERE assessments.shadow_mode = 0
    AND prediction_errors.target_type = 'benefit_continuous'
    AND prediction_errors.outcome_label_status = 'observed'
    AND prediction_errors.calibration_scope = 'proceeded_edits_only'
    AND prediction_errors.guidance_packet_id IS NULL
    AND COALESCE(prediction_errors.benefit_guidance_influenced, 0) = 0
```

Recommended enum/check constraints before implementation: `prediction_errors.target_type IN ('risk_binary','benefit_binary','benefit_continuous')`, `prediction_errors.outcome_label_status IN ('observed','censored','counterfactual')`, `prediction_errors.calibration_scope IN ('proceeded_edits_only','guided_edit','shadow','canary','benchmark')`, `sanction_events.status IN ('candidate','active','expired','invalidated','revoked')`, and `risk_snapshots.status IN ('genesis','candidate','shadow','canary','active','rolled_back','rejected')`.

Prediction-error NULL policy: binary targets populate `predicted_probability`, `actual_outcome`, `residual`, `brier_error`, and `log_loss`; `predicted_value`, `actual_value`, and `squared_error` remain NULL. Continuous benefit targets populate `predicted_value`, `actual_value`, `residual`, and `squared_error`; `predicted_probability`, `actual_outcome`, `brier_error`, and `log_loss` remain NULL.

**Hash-chain rule** (idiom from Aegis, reimplemented in stdlib `hashlib` — no code copied):
For **assessments**: `integrity_hash = sha256(canonical({id, created_at, recommended_decision, sha256(request_json), sha256(response_json), previous_hash}))` — it covers `sha256(response_json)`, the canonical response that is the source of truth, not just the request. For **outcomes**: `integrity_hash = sha256(canonical({id, assessment_id, action_id, guidance_packet_id, terminal_status, actual_result, recorded_at, previous_hash}))`. For **post-assessment guardrails**: `integrity_hash = sha256(canonical({id, assessment_id, current_head, scope_drift_detected, sha256(unexpected_files_json), sha256(pre_edit_symbol_diff_json), sha256(actual_symbol_diff_json), symbol_change_mismatch, sha256(contract_surface_changes_json), guidance_packet_id, safe_scope_status, sha256(risky_scope_triggered_json), sha256(risky_scope_actions_triggered_json), sha256(completed_checks_json), sha256(missing_checks_json), sha256(failed_checks_json), necessity_evidence_present, pre_commit_decision, sha256(reasons_json), recorded_at, previous_hash}))`. For **model guidance packets**: `integrity_hash = sha256(canonical({guidance_packet_id, assessment_id, action_id, decision, sha256(binding_json), sha256(advisory_json), sha256(provenance_json), calibration_scope, created_at, previous_hash}))`. For **sanction events**: `integrity_hash = sha256(canonical({sanction_id, assessment_id, action_id, repo_id, risk_snapshot_id, assessed_commit, guidance_packet_id, sanction_type, sanction_scope, status, ratified_by, rationale, safe_scope_hash, risky_scope_hash, required_controls_hash, control_blueprint_id, risk_report_hash, expires_at, invalidated_reason, created_at, previous_hash}))`. Learning rows (`prediction_errors`, `learned_risk_facts`, `risk_snapshots`) use the same append-only `previous_hash`/`integrity_hash` pattern. `previous_hash` = prior row's `integrity_hash`. `validate_chain()` re-walks rows and recomputes. Tamper-evident with zero deps.

**Concurrent writer rule:** every hash-chain append must be serialized inside a write transaction. The adapter opens the DB with `PRAGMA busy_timeout`, then uses `BEGIN IMMEDIATE` before reading the tail hash, computing the new `integrity_hash`, and inserting the row. WAL permits concurrent readers, but it does not by itself protect the read-tail-hash -> compute -> insert sequence from two writers racing against the same prior hash. CLI, MCP, dashboard-adjacent verify flows, and background learning jobs must all write through the same store adapter. An optional `.pebra/write.lock` may provide an extra process-level advisory lock, but the SQLite transaction is the required correctness boundary.

**Rebuildable projection exemption:** `criticality_cache`, `architecture_nodes`, `architecture_edges`, `architecture_anchors`, and `architecture_domains` are derived caches, not decision records. They are exempt from row-level hash chains; their integrity comes from rebuildability from the repo scan, AST/import graph, `graph_commit`, and content hashes. `validate_chain()` skips these projection tables and may rebuild them when stale or corrupt.

**Guided-outcome learning note:** model guidance changes model behavior. Outcomes and prediction errors produced under a guidance packet must carry `guidance_packet_id`. Precedence rule: `guidance_packet_id IS NOT NULL` implies `calibration_scope='guided_edit'`, enforced by a database check where practical and by adapter assertion otherwise. Guided rows must not be mixed with unguided outcomes without stratification, or PEBRA can learn a biased estimate of the model's natural failure rate. The calibration view also requires `guidance_packet_id IS NULL` as defense in depth.

**Guidance compliance learning:** `pebra_verify` turns model guidance into learnable labels. It records whether the edit respected `safe_scope`, which `risky_scope` entries fired, which action enums fired, which required checks were completed/missing/failed, whether the model supplied necessity evidence for `avoid_unless_required`, and the resulting verify decision. These are not hardcoded model syntax; they are dynamic labels derived from the stored guidance packet and the actual diff/check evidence. They feed guided-learning reports and future learned facts.

**Shadow mode** is the cold-start answer: v1 logs every assessment with `shadow_mode=1`; the agent/human proceeds normally; outcomes accumulate; when there's enough data, flip to `shadow_mode=0` and the risk `calibration_data` view feeds calibration — **no re-architecting.** Benefit calibration has separate min-N thresholds and may remain `pending_min_n` while risk calibration promotes.

**Learning-memory split:** raw prediction rows record what PEBRA predicted; outcome rows record what happened; `prediction_errors` record how wrong each observable probability was; `learned_risk_facts` record what PEBRA inferred from many errors; `risk_snapshots` decide which learned facts and parameter sets are active. Learned knowledge is never overwritten in place: a new fit creates a candidate snapshot, shadow/canary gates evaluate it, and promotion only advances the active snapshot pointer.

**Risk/benefit promotion split (AD-29):** one outcome stream feeds both risk and benefit learning, but promotion gates are separate. Risk calibration promotes or rolls back on risk targets only (`p_success`, `p_event.*`, `p_recurrence` when treated as harm probability). Benefit calibration promotes or rolls back on benefit targets only (`immediate_benefit_realized`, `maintainability_delta`, `review_cost`, `expected_rework_cost`, `technical_debt_interest`). Risk promotion never waits for benefit promotion. Benefit promotion never recomputes or invalidates a previously promoted risk snapshot. A snapshot record may contain both sections, but each section has its own `status`, `activated_at`, `min_n`, and promotion metrics.

Examples of learned facts:

```text
dependency_upgrade:major has signed_bias +0.16 over 84 observed outcomes
wildcard import edges have low reliability in this repo
auth patches with targeted tests have high p_success
src/api/billing/** repeatedly produces high-loss contract breaks
models exceed safe_scope on broad_refactor requests in this repo
dependency upgrades touched under auth scope often trigger reassessment
tests/test_auth.py catches most auth-guided regressions
```

Safe measurement facts can be auto-promoted after gates pass. Value/policy facts (`criticality` bumps, threshold changes, business-damage changes, widening `safe_scope`, downgrading `risky_scope.action`, or removing a required check) stay `status=candidate` with `requires_human_ratification=1`.

**Prediction target vocabulary:** `prediction_errors.target` uses exact canonical names and `prediction_errors.target_type` prevents invalid metric aggregation.

```text
risk_binary:
  p_success
  p_event.<event_class>
  p_recurrence_as_harm

benefit_binary:
  immediate_benefit_realized
  task_accepted
  recurrence_avoided

benefit_continuous:
  maintainability_delta
  review_cost
  expected_rework_cost
  technical_debt_interest
  net_benefit_score
```

Risk binary targets use Brier/log-loss. Benefit binary targets also use Brier/log-loss, but are evaluated in `benefit_binary_calibration_data`. Benefit continuous targets use residual/MSE/MAE and are evaluated in `benefit_continuous_calibration_data`. PEBRA must not average Brier/log-loss and continuous-error metrics into one promotion score.

**Benefit variance guard:** `Var(benefit)` may narrow only from observed benefit outcomes such as measured maintainability deltas, observed review cost, observed rework/recurrence, or verified technical-debt-interest evidence. Cross-fact confidence propagation, LLM confidence, unratified strategic value, or generic learned optimism must not narrow `Var(benefit)` because narrowing benefit variance raises RAU.

**Benefit guidance selective-label guard:** benefit guidance influences which edits proceed, so observed benefit outcomes are policy-conditioned. Rows affected by value guidance must set `benefit_guidance_influenced=1`. Default benefit calibration views exclude them; later models may stratify or explicitly include guidance as a calibration feature. This prevents PEBRA from learning that its preferred maintainability-improving actions were beneficial merely because it selected those actions.

**Core purity for learning:** `core/prediction_error.py`, `core/outcome_labels.py`, `core/promotion_evaluator.py`, `core/snapshot_reconciler.py`, and the snapshot/fact resolver inside `core/apply_snapshot.py` are pure functions. They receive already-loaded rows or dataclasses and return labels, errors, candidate facts, metrics, or active-snapshot decisions. They do not import `sqlite3`, read files, call git, or mutate the active snapshot pointer. Learning write orchestration lives in `app/learning_controller.py`; persistence, row loading, hash-chain writes, and active-snapshot updates live in `adapters/learning_store.py` behind `LearningPort`.

**Snapshot reapplication read path:** `core/apply_snapshot.py` is the pure function that makes learned facts affect the next edit:

```text
apply_snapshot(raw_inputs, active_snapshot, promoted_facts) -> adjusted_inputs
```

It runs after request validation and evidence collection but before score normalization, expected loss, RAU, edit confidence, variance propagation, and gates. It may adjust measurement inputs (`p_success`, `p_event.*`, `source_reliability`, `evidence_quality`, variance, repo-risk priors). It must not rewrite formulas, mutate the active snapshot, silently lower criticality, or auto-apply unratified value/policy facts.

Conflict resolution is deterministic: most-specific scope wins (`symbol > file/path glob > dependency > action_type > global`). If two active facts have the same specificity, the best-calibrated / highest-evidence fact wins; if still tied, the lowest stable `fact_id` wins. Weighted blending is deferred until AD-20 composition is enabled.

AD-16 is the v1 default and the k=1 fallback. Phase 6 upgrades this to top-k composition through `core/topk_composer.py`: candidate learned facts are partitioned by target, ranked by scope specificity and evidence quality, and pooled per target. Probability targets use reliability-weighted logarithmic pooling; [0,1] reliability targets use weighted arithmetic pooling.

Phase 7 adds `core/scope_dag_resolver.py`: a typed scope/action DAG stored inside `risk_snapshots.metrics_json.scope_dag`. The DAG is data, not a model. It provides deterministic dominance traversal for scopes such as repo, path glob, symbol, dependency, and action type, while `apply_snapshot` remains the only function that writes adjusted inputs.

**Decay-by-weight, not deletion:** learned facts are append-only, but `apply_snapshot` uses their `effective_weight`, not raw `base_weight`. `risk_fact_decay.py` computes effective weight from repo churn in the fact's scope, confirming outcome count, and prior Brier/log-loss contribution. Decay changes recall strength; it does not rewrite or delete the ledger row.

**Counterfactual promotion and reconciliation:** `promotion_evaluator.py` replays historical assessments with and without a candidate learned fact and compares Brier/log-loss. `snapshot_reconciler.py` periodically rebuilds a candidate snapshot from the raw append-only ledger and compares it with the active snapshot. If drift is too high, promotion freezes and requires review.

**Learning-loop and product-quality evaluation:** `benchmarks/flow/replay.py` and `core/learning_eval.py` stream historical assessments in chronological order and compare active learned snapshots against a genesis/no-learning baseline. This proves whether the learning loop improves calibration instead of merely accumulating facts. Phase 5b expands this into the product benchmark: deterministic flow regression, structural agreement, outcome-oracle calibration, learning lift, and agent-efficacy A/B runs.

The executable benchmark layout is:

```text
benchmarks/flow/
  manifests/*.yml
  corpus/
    requests/*.json
    outcomes/*.json
    expected_decisions/*.json
  adapters/
    structural/
      legacy_codeindex_adapter.py
      gitnexus_external_adapter.py
    jit/
      apachejit_loader.py
      jit_defects4j_loader.py
    agent/
      swebench_runner.py
  replay.py
  scorecard.py
```

`core/learning_eval.py` owns only pure metric primitives: Brier score, log loss, ECE bin aggregation, false-proceed/false-block arithmetic, decision-rate summaries, and lift arithmetic. `benchmarks/flow/scorecard.py` owns heavy benchmark analysis: pandas/scipy aggregation, bootstrap confidence intervals, AUC-PR/AUC-ROC, reliability diagrams, plots, and report rendering.

The benchmark tracks are:

| Track | Baseline / Oracle | Question | License / Scope |
|---|---|---|---|
| Deterministic flow regression | frozen golden corpus | Do identical inputs, snapshots, and models produce byte-identical scores, decisions, guidance, and guardrail outputs? | runs on every commit |
| Structural agreement | CodeGraph-derived PEBRA facts vs legacy codeindex / Graphify / GitNexus external reports | Does PEBRA's affected-area / centrality signal agree with established impact tools? | comparators are external-only, never shipped as decision sources |
| Calibration oracle | ApacheJIT / JIT-Defects4J labels plus logistic JIT-DP baseline | Are `p_success` and `p_event.*` calibrated and discriminative? | dataset version pinned |
| Learning lift | genesis/no-learning snapshot | Does active learning beat cold-start after outcomes accumulate? | chronological replay |
| Agent efficacy | SWE-bench Verified/Live with-vs-without PEBRA | Do guided agents introduce fewer regressions without losing resolved rate? | long-running benchmark tier |
| Comparator | TDAD-style graph-impact regression-reduction work when available | How does PEBRA compare to the nearest graph-impact agent-safety baseline? | comparator-only |

GitNexus may be used as an external benchmark comparator, like one model benchmarking against another. It is never imported, vendored, shipped, or required by PEBRA.

Scorecard math:

```text
For prediction p_i in (0,1) and observed outcome y_i in {0,1}:
  residual_i = y_i - p_i
  brier_i = (p_i - y_i)^2
  log_loss_i = -[y_i * ln(p_hat_i) + (1 - y_i) * ln(1 - p_hat_i)]
  p_hat_i = clip(p_i, LOG_LOSS_CLIP_EPS, 1 - LOG_LOSS_CLIP_EPS)

Brier = mean(brier_i)
LogLoss = mean(log_loss_i)
signed_bias = mean(y_i - p_i)

ECE = sum_m (|B_m| / N) * |observed_rate(B_m) - predicted_mean(B_m)|
lift_lower_is_better = M_baseline(genesis) - M_learned(active)
lift_higher_is_better = M_learned(active) - M_baseline(genesis)

false_proceed_rate = count(proceed and harmful) / count(harmful)
false_block_rate_c0_c2 = count(held and safe and criticality in {C0,C1,C2}) / count(safe and criticality in {C0,C1,C2})
```

Brier/log-loss are lower-is-better promotion gates, so positive lift is `baseline - learned`. AUC-PR, AUC-ROC, and Spearman agreement are higher-is-better, so positive lift is `learned - baseline`. ECE is reported and used for reliability diagrams, but not alone as a hard gate because it is bin-sensitive. AUC-PR is the primary discrimination metric for defect/oracle corpora because harmful outcomes are usually imbalanced; AUC-ROC is secondary. Bootstrap confidence intervals and significance gates live in `benchmarks/flow/scorecard.py`, not in core.

Outcome labels use a consistent convention: `y=1` means harmful / bug-inducing and `y=0` means safe. Dataset adapters must map their oracle into this convention: SZZ/JIT corpora map bug-inducing commits to `y=1`; SWE-bench-style agent runs map failed or regression-introducing patches to `y=1`; live PEBRA outcomes map terminal failed/regression statuses to `y=1`.

Release/promotion gates:

| Gate | Failure Condition |
|---|---|
| Calibration | learned Brier/log-loss regresses against genesis or prior snapshot beyond the configured confidence interval |
| Safety | false-proceed rate increases |
| Over-blocking | C0-C2 false-block rate exceeds `max_false_block_rate` after enough labels |
| High-criticality safety | any C3/C4 decision weakens from held to proceed without ratified policy |
| Explainability | material decision flip or risk-band crossing lacks cited `fact_id` / `snapshot_id` |
| Determinism | regression mode is not byte-identical across repeated runs |
| Evidence sufficiency | corpus size is below `N_min` |

**Risk Observatory dashboard:** the dashboard is an operator-facing surface, not a benchmark UI. It answers: what is PEBRA doing in this repo right now, why did it decide that, is it becoming more trustworthy from real outcomes, did the agent stay inside the approved envelope, and can the operator audit/replay what happened?

It is parallel to CLI/MCP:

```text
cli/          one-shot human card
mcp_server/   agent integration
dashboard/    self-hosted Risk Observatory
core/         unchanged; no UI imports
```

`pebra dashboard` serves a self-hosted local web UI that reads SQLite/API/scorecard artifacts and renders existing facts. It does not recompute risk formulas, promote snapshots, edit policy, or mutate assessment state in v1.

**Multi-repo runtime model.** By default, `pebra dashboard` opens the current repo resolved by `adapters/paths.py`. If the machine registry exists, the dashboard may show a repo switcher listing registered repos, but each route is scoped: `/repos/<repo_id>/...` reads only that repo's `.pebra/pebra.db`. A missing registry does not prevent single-repo operation. `pebra dashboard --repo <path>` registers or refreshes that repo, then opens it. `pebra dashboard --all` is allowed to open the registry landing page, but it still reads per-repo DBs rather than a global learning store.

**Port assignment.** The declared base port is `9473`. The allocator uses this order:

1. If `<repo>/.pebra/dashboard.json` records a live PEBRA process for this repo and its port is reachable, reuse it.
2. If the requested port comes from `--port` or `PEBRA_PORT`, bind exactly that port and fail fast if unavailable.
3. If `--port 0` is supplied, ask the OS for an ephemeral port and record the actual bound port.
4. Otherwise try `9473`, then bounded auto-increment (`9474`, `9475`, ...), skipping ports already held by non-PEBRA processes.
5. `--instance N` is an escape hatch modeled after agentmemory: it maps to `9473 + N*100` so multiple explicit daemons can run side by side.

The Host allowlist and any printed/opened URL are built from the actually bound host/port, never from the requested default. PID-based stale detection should remove dead `dashboard.json` records. If a port is occupied by an already-running PEBRA daemon, the new command should ask that daemon to register/open the requested repo rather than starting a duplicate process.

Dashboard panels:

| Panel | Shows |
|---|---|
| Overview | decision mix, current risk posture, confidence distribution, C3/C4 count |
| Assessments | latest decisions, risk facts, why text, guidance packet, cited `fact_id` / `snapshot_id` |
| Risk | risk levels, expected damage trend, top risk drivers |
| Learning | Brier/log-loss from real recorded outcomes, signed bias, false-proceed / false-block |
| Guidance | safe-scope drift, risky-scope triggers, required checks completed |
| Replay | assessment -> guidance -> edit -> verify -> outcome timeline |
| Architecture | sensitive domains, high-reach files, architecture anchors |
| Audit | hash-chain status, provenance, snapshots, outcome links |

The dashboard never displays benchmark harness results. ApacheJIT, JIT-Defects4J, SWE-bench, TDAD, and GitNexus comparator reports remain developer/research artifacts under `benchmarks/flow`, CI artifacts, release notes, or docs. The dashboard displays only production/project state derived from PEBRA's assessment store, outcome store, learning snapshots, guidance compliance, architecture map, and audit chain.

Security posture follows the agentmemory viewer pattern: bind to `127.0.0.1` by default, require bearer authentication for API access, reject unapproved `Host` headers to prevent DNS rebinding, self-host all static assets, avoid remote CDNs, use a CSP nonce, and require explicit opt-in plus allowed-host configuration for non-loopback binding. A registry-backed dashboard token grants access to registered local repos shown by that dashboard; future per-repo tokens may narrow that scope, but v1 must at least display which repo each page is reading.

Playwright validation belongs to the dashboard surface. The headed visual E2E suite should assert that the dashboard opens on localhost, fixture-backed cards/tables/charts show the expected values, learning charts and reliability diagrams are nonblank, replay scrubbing works, guidance compliance renders safe/risky scope events, audit/hash-chain status renders, no console errors occur, and screenshots/traces are saved. Playwright validates visual fidelity and wiring; metric correctness remains covered by core tests and `benchmarks/flow` scorecards.

**Architecture-map memory:** the SQLite store also caches derived codebase structure separately from risk decisions. `architecture_nodes`, `architecture_edges`, `architecture_anchors`, and `architecture_domains` are rebuildable projections derived from CodeGraph facts plus PEBRA normalization. They answer "what is structurally central right now?" before scoring. They are not learned risk facts and do not replace `criticality_cache`; they feed `ArchitectureKnowledgeProvider` so PEBRA can distinguish high-reach architecture anchors from high-sensitivity domains.

Architecture freshness is CodeGraph-status based, not commit based. Assessment freshness still compares the assessment commit to current `HEAD`; architecture freshness asks CodeGraph to sync, then trusts only a clean `codegraph status --json`: initialized, zero pending added/modified/removed files, `index.reindexRecommended=false`, and no worktree mismatch. If sync repairs changes and the final status is clean, `graph_freshness=rebuilt`; if the final status is not clean, `graph_freshness=stale`.

The evidence-validity gate is the last gate before `proceed`: if `architecture_evidence.graph_freshness=stale` and `inspect_on_stale_arch_map=true` (default), the decision downgrades to `inspect_first`. This gate only downgrades an otherwise proceedable assessment; it never preempts a stricter `reject`, `ask_human`, `test_first`, or low-confidence result, and it is not sanction-convertible. `fresh`, `rebuilt`, and `unknown` do not fire the gate.

---

## 11. Reference Patterns & Attributions

| Pattern | Source | License | Use |
|---|---|---|---|
| SQLite WAL store, schema versioning, soft-delete | codeindex `store/db.py` | Apache-2.0 | adapt pattern (Apache-2.0 permits direct reuse with attribution + NOTICE if we choose to copy) |
| Blast-score `d + 0.5·transitive`, BFS over reverse import graph | codeindex `impact.py:62` | Apache-2.0 | adapt pattern / implement compatible adapter |
| Persistent code structure graph and symbol index | CodeGraph | MIT | required external graph engine; PEBRA normalizes facts and owns risk math |
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

## 12. Dependency Policy

- **Core import rule (not package dependency rule):** `core/` imports only deterministic stdlib + `core/`. It may use modules such as `dataclasses`, `typing`, `math`, `json`, `hashlib`, `fnmatch`, `uuid`, and pure parsing helpers such as `ast` when operating on already-supplied source text. It must not import I/O or surface modules such as `sqlite3`, `subprocess`, `argparse`, web frameworks, or dashboard code. This keeps the decision brain reproducible and auditable.
- **Purity enforcement:** `import-linter` must forbid `pandas`, `scipy`, `matplotlib`, `seaborn`, `datasets`, `pydriller`, `swebench`, `fastapi`, `starlette`, `uvicorn`, and `jinja2` inside `pebra.core`, and must forbid `pebra.dashboard` from being imported by `pebra.core` or `pebra.adapters` (the dashboard reads *through* the store/scorecard readers; nothing in the brain reads back from it). The dashboard's web-stack deps are hard runtime deps (shipped, not optional), so the linter contract — not their absence — is what keeps them out of `core/`. Benchmark math that must stay in core (`Brier`, `log_loss`, bin summaries, decision-rate arithmetic, lift arithmetic) is pure stdlib. Heavy stats, plots, confidence intervals, dataset loading, and agent runners live only under `benchmarks/`; the web/dashboard stack lives only under `dashboard/`.
- **PEBRA runtime dependencies:** the installed package should include the Python libraries needed for the designed product path:
  - `pyyaml` — `.pebra.yml` config parsing.
  - `radon` — Python LOC, complexity, Halstead, Maintainability Index.
  - `bandit` — Python SAST / security-sensitive operation evidence.
  - `numpy` — Monte Carlo sampling and array math for calibration reports.
  - `scikit-learn` — Platt/isotonic calibration, logistic stacking when enough outcomes exist.
  - `cryptography` — Ed25519 signing when signed audit chains are enabled.
  - `mapie` — conformal intervals when enabled by the calibration path.
  - `fastapi` / `starlette` — local dashboard/API surface for the Risk Observatory.
  - `uvicorn` — local dashboard server.
  - `jinja2` — dashboard HTML templating.
- **Required external runtime graph engine:** CodeGraph (`@colbymchenry/codegraph`, MIT) is the required precision graph backend. It is not a Python package, so it is not listed in `pyproject.toml`'s `project.dependencies`; installers and runtime checks must ensure the `codegraph` command is available and initialized for the repo. PEBRA consumes CodeGraph by subprocess (`codegraph sync --quiet`, `codegraph status --json`) and by read-only SQLite queries against `.codegraph/codegraph.db`. PEBRA owns all fan-in percentile math, confidence tiering, risk/benefit scoring, learning, and audit. CodeGraph version and extraction version are recorded in evidence and calibration scope.
- **External benchmark/comparison tools:** `sem`, legacy `codeindex`, Graphify artifacts, and GitNexus-style reports are comparison/enrichment references only. They may be used by benchmark adapters or research runs, but they are not the runtime source of record once CodeGraph is the required graph engine.
- **Developer dependencies:** `pytest`, `pytest-cov`, `ruff`, `mypy`, `import-linter`, `hypothesis`, `syrupy`, `nox`, `jsonschema`, `build`, `twine`, and `pre-commit`.
- **UI test dependencies:** `playwright` for headed visual E2E validation of the self-hosted dashboard. Playwright tests rendering/wiring against fixture store/API data; it does not validate metric formulas.
- **Benchmark dependencies:** keep benchmark tooling out of normal runtime and core development:
  - `bench`: `pandas`, `scipy`, `datasets`, `matplotlib`, `seaborn`.
  - `bench-szz`: `pydriller` for SZZ/git-mining benchmark labels.
  - `bench-agent`: `swebench` and benchmark agent-runner tooling.
  - `bench-external`: user-provided external comparators such as GitNexus, TDAD implementations, or commercial agent runners. These are never vendored, imported, shipped, or required by PEBRA.
- **Hard rule:** no GPL/AGPL runtime deps, ever. pyDecision stays reference-only.

Benchmark scorecards must record both library versions and dataset/comparator identities: dataset name, dataset version/split/commit, comparator tool version/commit, PEBRA git commit, `risk_snapshot_id`, `prediction_error_model_id`, and `calibration_scope`. Benchmark numbers are comparable only when both code and data are pinned.

**Explicit non-goals for v1:** no GitNexus-style graph database, Cypher, embeddings, SaaS web app, auto-wiki, cross-repo contract graph, full PDG/taint engine, or LLM cluster enrichment inside PEBRA. CodeGraph is the required external graph engine, but PEBRA does not become a graph platform: it normalizes CodeGraph facts into risk-relevant evidence and keeps the decision brain, learning, audit, and dashboard as its product surface. A self-hosted Risk Observatory and a minimal machine repo registry are allowed as runtime/operator surfaces; neither is part of `core/`, and neither may become a general web platform or global learning store.

---

## 13. Architecture Decisions (resolved)

- **AD-1 — Event-class-aware criticality floor.** Define `CONSEQUENCE_BEARING_EVENTS` in `constants.py`; the floor `max(elicited, STAGE_MAP[stage])` applies only to those events. `test_regression`/`review_burden` keep their elicited disutility. *Reproduces the worked example: `test_regression` stays 0.40, `expected_loss=0.10`, `EU=0.39`.* Enforced in `score_math._apply_floor`. **(Spec updated — §5.5 now states the event-class-aware floor.)**
- **AD-2 — `RAU < 0` is a formal gate** (not just a band), placed after the expected-loss gate → `ask_human` by default (`reject` only if `ask_on_negative_rau=false`). Enforced in `decision_engine`. **(Spec updated — now a numbered gate in §8.2, with `ask_on_negative_rau` in §12.)**
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
- **AD-13 — PEBRA does not become GitNexus.** Graph DBs, embeddings, Cypher, cross-repo contract graphs, PDG/taint engines, and LLM cluster enrichment are deferred or adapter-only. PEBRA's product surface is the decision controller and autonomy governor, not the graph platform. The Risk Observatory and minimal repo registry are operator/runtime surfaces, not a graph platform.
- **AD-14 — Autonomous risk learning is snapshot-gated.** PEBRA may autonomously update measurement parameters (`p_success`/`p_event` calibration, source reliability, edge-confidence weights, variance estimates, repo risk memory) only by deterministic recomputation over the outcome store. It may not mutate a live decision mid-flight: each assessment pins `risk_snapshot_id` and `prediction_error_model_id`; candidates are evaluated as `shadow`/`canary`; promotion advances the active snapshot pointer; rollback points back to the prior snapshot. LLM-authored self-mutation of gate-driving scores, thresholds, or formulas is a non-goal. **(Spec ratified — §12.1–§12.3.)**
- **AD-15 — Prediction error uses proper scoring rules and two-tier routing.** PEBRA learns from observable probability errors, not directly from RAU. Every `p_success` and `p_event_j` prediction gets a `prediction_id`; after outcomes, `prediction_error.py` computes `residual = actual - predicted`, `brier_error = residual^2`, and log loss. These errors feed Tier-1 autonomous calibration. RAU/risk-budget/confidence/decision errors are diagnostics only: first correct measurement error, then treat residual value/policy patterns as human-ratified suggestions. Outcome labels (`observed`, `censored`, `counterfactual`) and calibration scope (`proceeded_edits_only`, `guided_edit`, `shadow`, `canary`, `benchmark`) must be stored separately to avoid selective-label self-reinforcement. **(Spec ratified — §12.1–§12.3.)**
- **AD-16 — Snapshot reapplication / prior resolution.** Learning affects the next edit only through `apply_snapshot(raw_inputs, active_snapshot, promoted_facts) -> adjusted_inputs`, which runs before scoring and gates. It matches promoted learned facts to the new action by scope, applies deterministic precedence (`symbol > file/path glob > dependency > action_type > global`; best-calibrated / highest-evidence fact wins within the same specificity), and adjusts only measurement inputs unless a value/policy fact has human ratification. The scoring formulas remain unchanged. **(Spec ratified — §12.4.)**
- **AD-17 — Learned facts decay by recall weight, never by deletion.** PEBRA keeps the append-only fact ledger intact, but `apply_snapshot` uses `effective_weight = base_weight * exp(-scope_change_count / decay_strength)`. `scope_change_count` is churn in the fact's scope, not wall-clock time. High-evidence facts decay more slowly; stale facts fade without losing auditability. **(Spec ratified — §12.5.)**
- **AD-18 — Promotion requires counterfactual replay and reconciliation.** A learned fact is promoted only if replaying historical assessments with the fact improves or does not regress Brier/log-loss relative to replaying without it. Snapshots are periodically rebuilt from the raw ledger and compared to the active snapshot; excessive drift freezes promotion and routes to review. **(Spec ratified — §12.6.)**
- **AD-19 — Learning-loop value must be evaluated as a stream.** PEBRA validates learning by replaying assessments chronologically, comparing active learned snapshots against a genesis/no-learning baseline, and reporting calibration improvement, false-proceed drift, contradiction rate, staleness distribution, and rework efficiency. **(Spec ratified — §14.4.)**
- **AD-20 — Top-k learned fact composition.** Phase 6 replaces single-winner reapplication with top-k composition per target. Matching facts are partitioned by target, ranked by specificity and evidence quality, and combined by reliability-weighted logarithmic pooling for probability targets or weighted arithmetic pooling for [0,1] reliability targets. Correlated facts cannot stack unboundedly because normalized weights sum to 1. AD-16 remains the v1/k=1 fallback. **(Spec ratified — §12.8.)**
- **AD-21 — Typed scope/action DAG.** Phase 7 introduces a deterministic scope graph for learned-fact matching: repo, path glob, symbol, dependency, and action-type nodes with dominance edges. The DAG is serialized in snapshot JSON and hash-chained as data. It replaces string-order heuristics with maximal-element / longest-prefix dominance traversal, while rejecting RL, softmax node weights, embeddings, and non-deterministic traversal. **(Spec ratified — §12.9.)**
- **AD-22 — Architecture knowledge is first-class evidence.** CodeGraph is the required external graph engine for production graph evidence. AD-22 makes PEBRA repo-architecture agnostic by deriving a risk-relevant architecture map from CodeGraph nodes, edges, files, and metadata: symbol fan-in percentiles, fan-out, import/call/reference reach, cycle participation, cross-directory bridge proxy, entrypoint signals, coarse domains, and capability hints. PEBRA owns the normalized features and graph math; CodeGraph supplies graph facts only. Graph incompleteness or stale CodeGraph status lowers confidence or triggers the evidence-validity gate; it never fabricates blast. External artifacts such as `ARCHITECTURE.md`, Graphify, legacy codeindex, or GitNexus reports are benchmark/comparison sources, not production prerequisites. The provider runs before scoring and may raise affected-area, review-cost, `p_event`, and explanation pressure for architecture anchors / god nodes. It must not auto-raise criticality without capability evidence or human-ratified policy. **(Spec ratified — §4.3.)**
- **AD-23 — Model guidance is the pre-edit autonomy envelope.** PEBRA remains model-free in scoring and reapplication, but it renders deterministic model guidance for the editing agent. Binding fields (`safe_scope`, `risky_scope`, `required_checks_before_commit`) are derived from the approved action envelope, gates, project policy, architecture evidence, and learned facts, then enforced by `pebra_verify`. `risky_scope` entries carry an action enum: `requires_reassessment`, `avoid_unless_required`, or `forbidden`. Advisory fields (`risk_facts`, `why`, `suggested_inspection`, `safer_alternative`) steer the model but are not hard gates. JSON is only the canonical audit representation; adapters may render the same facts as prompt text, MCP payloads, or PR cards. The packet is hash-chained and logged because guided outcomes must be calibrated separately from unguided outcomes. **(Spec ratified — §12.10; example rendering — §18.3.)**
- **AD-24 — Repo-scoped runtime state.** PEBRA supports many local repos by making `<repo>/.pebra/pebra.db` the authoritative store for that repo's assessments, outcomes, learning snapshots, architecture cache, guidance packets, and dashboard state. `adapters/paths.py` resolves the current repo by walking up for `.pebra/` or `.git/`; MCP/CLI/dashboard all use the same resolver. A small machine registry records known repos and ports for discovery only; it does not hold learned facts or policy. Worktrees are detected and isolated by default so parallel agent branches do not share learning accidentally. Every hash-chain append is serialized with `BEGIN IMMEDIATE` and `busy_timeout` before reading the tail hash. **(Spec ratified — §12.11.)**
- **AD-25 — Dashboard port assignment is deterministic but collision-safe.** The Risk Observatory declares base port `9473`, reuses a live repo-local `dashboard.json` port when available, honors explicit `--port`/`PEBRA_PORT` as fail-fast pins, supports `--port 0` for OS-assigned ephemeral ports, auto-increments only when using defaults, and supports `--instance N` as an agentmemory-style escape hatch (`9473 + N*100`). Host allowlists and printed URLs use the actually bound port. **(Spec ratified — §14.5.)**
- **AD-26 — Controlled high-risk mode is a mode, not a sixth decision.** Risky-but-mandated work remains scored honestly; sanctioning or business necessity never lowers expected loss, RAU, confidence, or risk budget. Before ratification the decision is normally `ask_human`; after authorized risk acceptance and mandatory controls, the decision may become `proceed` with `risk_mode=controlled_high_risk`, `requires_confirmation=true`, and binding controls. Sanctions are bound to the risk profile they approved and invalidate on scope, evidence, or symbol-change drift. Any high-risk `ask_human`, `reject`, or `controlled_high_risk` route must include `high_risk_triggers[]` so the model and auditor see why the route fired and which controls map to it. **(Spec ratified — §12.12.)**
- **AD-27 — Symbol-level risk resolution is the canonical risk model.** C4 paths, payment files, and god nodes tell PEBRA where to inspect harder; they do not by themselves drive formulas or trigger controlled high-risk mode when symbol evidence exists. `SymbolDiffProvider` maps edits to changed symbols and `core/change_classifier.py` classifies the change as `COSMETIC`, `DIRECTIVE`, `TEST_ONLY`, `BEHAVIORAL`, `CONTRACT`, `SIDE_EFFECT`, or `UNKNOWN`. The classified symbol/scope evidence feeds normal `p_event`, Affected Area, review cost, risk reports, guidance, high-risk triggers, verification, and learning buckets. High-risk escalation is a downstream annotation/control path that requires critical context plus a consequential symbol/change. `pebra_verify` reruns the full classifier on the actual diff and escalates if the post-edit change kind is more severe than the pre-edit packet. **(Spec ratified — §12.13.)**
- **AD-28 — Benefit is comparative, multi-horizon, maintainability-aware, and risk-constrained.** PEBRA optimizes auditable net value under risk constraints, not risk minimization. `core/benefit_model.py` resolves a provenance-traced `benefit_breakdown` into the scalar `benefit` and `Var(benefit)` consumed by the existing RAU formula. Components include immediate task value, maintainability delta, technical-debt interest, recurrence/durability risk, information value, and strategic/business value. Maintainability is a first-class economic outcome: pre-edit deltas are derived from a concrete proposed patch AST/diff when available, projected only when no patch exists, and measured after `pebra_verify` sees the actual diff. Benefit deltas are computed by before/after metric comparison, directional normalization, and future-exposure weighting across complexity, modularity, coupling, cohesion, testability, analyzability, modifiability, duplication, encapsulation, API surface, observability, operability, and recurrence. Recurrence and durability probabilities may be learned from observable outcomes and converted into expected rework cost; strategic value, discount rates, maintainability weights, and debt tolerance are Tier-2 value policy and require ratification. Positive maintainability benefit follows confirm-before-credit; debt interest and expected rework are applied immediately. Net-benefit style ranking is the primary alternative-comparison lens, while ICER-style ratios are diagnostic only. **(Spec ratified — §12.14.)**
- **AD-29 — Benefit learning is decoupled from risk learning.** Risk and benefit share outcome records, but use separate prediction targets, calibration views, metrics, min-N gates, and promotion gates. Risk promotion depends only on risk calibration and never waits for benefit rows. Benefit promotion matures asynchronously and never recomputes or invalidates promoted risk calibration. `Var(benefit)` may narrow only from observed benefit outcomes, not generic confidence propagation or unratified value claims. Benefit-guidance-influenced rows are flagged and excluded or stratified by default to avoid a self-reinforcing optimism loop. **(Spec ratified — §12.15.)**

---

## 14. Build Sequence

- **Phase 0 — stdlib-core skeleton → first runnable milestone.** `models.py`, `constants.py`, `score_math.py`, `benefit_model.py`, `score_normalizer.py`, `weight_resolver.py`, `confidence_gate.py`, `request_validator.py`, `candidate_parser.py`, `assessment_builder.py`, `decision_engine.py`, `explanation_generator.py`, `model_guidance.py`, `core/change_classifier.py`, `core/high_risk_controls.py`, `app/assess_controller.py`, `app/accept_risk_controller.py`, `ports/repository_registry_port.py`, `ports/symbol_diff_port.py`, `ports/sanction_port.py`, core-facing ports used by Phase 0 (`EvidenceProvider`, `BlastRadiusProvider`, `SymbolDiffProvider`, `StorePort`, `OutcomePort`, `CalibrationPort`, `RepositoryRegistryPort`, `SanctionPort`), base tables (`assessments`, `model_guidance_packets`, `sanction_events`), guidance-packet write path, risk-acceptance write path, `adapters/paths.py`, `adapters/repository_registry.py`, `adapters/ast_diff_adapter.py`, `adapters/ast_import_graph.py`, `adapters/git_adapter.py`, `adapters/store/db.py`, `adapters/sanction_store.py`, `.pebra/.gitignore` initialization, `cli/assess.py`, `cli/accept_risk.py`, `examples/login_patch.json`.
  **Milestone:** `python -m pebra assess examples/login_patch.json` prints the human card while `core/` remains stdlib-only. The fixture is the spec §10 worked example and must reproduce expected loss `0.10`, C3 risk budget `50%`, EU `0.39`, RAU `0.31`, confidence `0.83`, and `proceed` with confirmation.
- **Phase 1 — autonomy guardrails first:** `post_assessment_guardrails`, `app/verify_controller.py`, `change_verifier_port`, `contract_surface_port`, `git_change_verifier`, `pebra verify` CLI, evidence freshness check, actual-diff drift check, post-edit full symbol reclassification, measured post-edit benefit deltas from the actual diff, guidance compliance logging, dry-run refactor requirement, pre-commit/PR risk card. The `pebra_verify` MCP tool ships in Phase 3 with the MCP server.
- **Phase 2 — evidence quality enrichment:** `yaml_config`, `query_validator`, `ArchitectureKnowledgeProvider`, `architecture_map` adapter, unified content-hash import-graph cache, AST edge confidence, depth buckets, entrypoint signal, import-cycle detection, graph incompleteness and parse-failure uncertainty, repo-relative anchor/god-node metrics, `radon`, `bandit`.
- **Phase 3 — MCP + outcomes:** `mcp_server` (`pebra_compare`/`pebra_assess`/`pebra_verify`/`pebra_accept_risk`/`pebra_record_outcome`), `app/record_outcome_controller.py`, `outcome_logger`, `calibration_store` (shadow read), `cli/record_outcome`.
- **Phase 4 — CodeGraph graph engine:** required CodeGraph adapter, read-only `.codegraph/codegraph.db` queries, `codegraph sync --quiet` + `status --json` freshness gate, symbol fan-in percentiles, edge-confidence tiers, and provenance/version capture.
- **Phase 5 — calibration + learning loop:** `LearningPort`, `app/learning_controller.py`, `prediction_error.py`, `outcome_labels.py`, `learning_store`, `apply_snapshot` (including folded snapshot/fact resolution), `risk_fact_decay`, `promotion_evaluator`, `snapshot_reconciler`, `contradiction_gate`, raw `outcomes`, `prediction_errors`, `learned_risk_facts`, `risk_snapshots`, rolling Brier/log-loss reporter, benefit outcome labels (`reverted`, `reedit_required`, `issue_reopened`, measured maintainability delta), separate risk/benefit calibration views, decoupled promotion gates, shadow/canary promotion gates, flip `shadow_mode=false`, SWE-bench pilot. Canary/benchmark rows are not included in the default calibration views; they feed separate validation reports unless a future calibrated model explicitly stratifies by `calibration_scope`.
- **Phase 5b — executable product benchmark:** `benchmarks/flow/replay.py`, `benchmarks/flow/scorecard.py`, fixture corpora under `benchmarks/flow/corpus/`, benchmark manifests, deterministic flow regression, structural agreement adapters, JIT outcome-oracle loaders, chronological learning-lift replay, genesis/no-learning baseline, calibration-improvement report, false-proceed/false-block scorecard, and optional SWE-bench-style with-vs-without PEBRA agent runs.
- **Phase 5c — Risk Observatory dashboard:** `dashboard/server.py`, `dashboard/api.py`, `dashboard/ports.py`, local templates/static assets, `pebra dashboard`, repo switcher backed by the machine registry, current-repo default, `/repos/<repo_id>/...` route scoping, port reuse/auto-increment/`--instance` handling, stale PID detection, overview/assessment/risk/learning/guidance/replay/architecture/audit panels, localhost-first security, bearer auth, Host allowlist, CSP nonce, and no benchmark-result panels.
- **Phase 5d — dashboard visual E2E:** headed Playwright suite against fixture SQLite/API data, screenshots/traces, no-console-error checks, nonblank chart assertions, replay interaction checks, and stable `data-testid` selectors for critical cards/tables/charts.
- **Phase 6 — v1.5:** `topk_composer`, top-k learned fact composition, reliability-weighted logarithmic pooling, Monte Carlo (numpy), Ed25519 signing (cryptography), multi-language graphs, `pebra_explain`.
- **Phase 7 — v2:** `scope_dag_resolver`, typed scope/action DAG serialized in `risk_snapshots.metrics_json.scope_dag`, optional `scope_node_id` use in learned facts, deterministic dominance traversal.

---

## 15. Success Criteria (v1)

- `pebra assess` produces a correct, human-readable risk card from a JSON request while `core/` remains stdlib-only.
- `pebra assess` resolves the current repo consistently from subdirectories, initializes `<repo>/.pebra/`, writes `.pebra/.gitignore`, records repo metadata, and never stores learned facts outside the repo-local DB.
- The decision math reproduces the spec worked example exactly (see Appendix A).
- `core/` imports nothing outside stdlib (enforced by a test that `ast.walk`s for forbidden imports).
- Every decision is logged with a verifiable hash chain; outcomes can be recorded; the calibration view populates.
- Concurrent CLI/MCP/dashboard-adjacent writes cannot fork the hash chain because every append uses `BEGIN IMMEDIATE` and reads the tail hash inside the same transaction.
- Every model guidance packet is derived from PEBRA outputs, hash-chained, and enforceable by `pebra_verify`.
- Controlled high-risk approvals are hash-chained in `sanction_events`, bound to the approved risk profile, and invalidated when scope/evidence/symbol-change drift changes that profile.
- High-risk decisions are never bare: any `ask_human`, `reject`, or `controlled_high_risk` route caused by high-risk conditions includes `risk_mode`, `high_risk_triggers[]`, mapped control blueprint(s), and required controls or suppression reasons.
- Learned risk facts and active snapshots are stored append-only in SQLite, with every assessment pinned to the snapshot it used.
- The next assessment reapplies promoted learned facts through `apply_snapshot` before scoring, with deterministic scope precedence.
- Existing architecture maps are loaded before scoring; stale maps are detected by content hash and either refreshed by an adapter or force `inspect_first` when the rebuild fails.
- Cosmetic or safe test-only edits to C4/god-node/payment paths do not trigger controlled high-risk mode solely from file membership, while behavioral/contract/side-effect edits to consequential symbols do.
- Ordinary risk cards and JSON scores use the same symbol/scope evidence as controlled high-risk mode, so cosmetic/test-only edits in C4/god-node/payment files are not over-scored by file membership alone.
- `pebra_verify` reruns symbol-level classification on the actual diff and escalates if the actual change is more severe than the pre-edit proposed patch.
- `benchmarks/flow` can replay a frozen corpus deterministically, compare learning-on against a genesis/no-learning baseline, run structural agreement checks, evaluate probability calibration against labeled JIT corpora, and run with-vs-without PEBRA agent efficacy benchmarks when benchmark-agent tooling is installed.
- `pebra dashboard` serves a read-only self-hosted Risk Observatory that displays live/project state from PEBRA stores without showing benchmark harness results or mutating policy/learning state.
- `pebra dashboard` supports many local repos through the machine registry while keeping each repo's state isolated; port conflicts are handled by reuse, explicit pinning, default auto-increment, or `--instance N`.
- The dashboard visual E2E suite can run headed with Playwright, validate fixture-backed cards/charts/replay/audit rendering, and save screenshots/traces for review.
- Stale learned facts decay by effective weight based on scoped churn, without deleting ledger history.
- Snapshot promotion is backed by counterfactual replay and can be frozen by reconciliation drift.
- Top-k composition can combine multiple matching learned facts per target without unbounded stacking.
- The typed scope/action DAG is available as the ratified v2 matching model when glob/string matching becomes insufficient.
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
