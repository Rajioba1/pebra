# PEBRA: Pre-Edit Benefit-Risk Assessment Tool for Coding Agents

## Tool-Oriented Technical Spec

Updated June 2026. PEBRA is a practical pre-edit decision tool for coding agents. License notes are technical planning notes, not legal advice.

---

## 1. Goal and Non-Goals

PEBRA helps a coding agent decide what to do before it edits code.

For each task, PEBRA compares candidate actions and returns exactly one decision:

```text
proceed | inspect_first | test_first | ask_human | reject
```

The core question is:

> Is this edit worth doing now, or should the agent inspect more, run tests, ask a human, or choose a narrower change to prevent overengineering?

PEBRA is:

- An MCP server agents can call before code edits.
- A CLI developers can run locally.
- An evidence discovery layer that measures repo structure before scoring risk.
- A confidence-based editing controller.
- A scoring layer that compares candidate edit and information actions.
- A learning loop that records outcomes and calibrates future scores.

PEBRA is not:

- A replacement for tests, CI, sandboxing, or code review.
- A general policy engine.
- A new code graph engine from scratch.
- A generic MCDA method catalogue.
- A claim that every score is a true probability.

PEBRA's distinctive behavior:

```text
candidate action
-> evidence discovery
-> expected utility / RAU
-> edit confidence
-> decision gate
-> proceed, inspect, test, ask, or reject
```

When confidence falls, the agent should not guess harder. It should gather better evidence via online searches or local repo references if available, reduce edit scope, or ask for help.

---

## 2. Glossary and Canonical Vocabulary

This section is the source of truth for names used elsewhere in the spec.

### 2.1 Decision Enum

`decision` must be one of:

| Value | Meaning |
|---|---|
| `proceed` | The selected edit action may be performed, subject to `requires_confirmation` and policy gates |
| `inspect_first` | Gather non-test repo evidence before editing |
| `test_first` | Run or add targeted tests before editing |
| `ask_human` | User or reviewer input is required before editing |
| `reject` | Do not perform this action |

The following are not decision values:

- `do_first`
- `completed`
- `proceed_with_confirmation`
- `reject_upgrade`
- `ask_human_before_edit`

Use separate fields instead:

```json
{
  "recommended_decision": "proceed",
  "requires_confirmation": true,
  "action_status": "pending"
}
```

### 2.2 Action Status

`action_status` must be one of:

```text
pending | completed | skipped | rejected
```

Status describes whether an action has happened. It is not a decision.

### 2.3 Action Type

`action_type` must be one of:

| Value | Meaning |
|---|---|
| `edit` | Changes code, dependencies, schema, config, or tests |
| `information` | Gathers evidence without changing production code |

Edit actions are ranked by expected utility, risk-adjusted utility, confidence, and gates.

Information actions are ranked by low cost and expected uncertainty reduction. They do not use adverse-event loss unless they can change code, state, data, or external systems.

### 2.4 Source Provenance

Every score must separate provenance type from provider.

Use `source_type` for semantic provenance:

| `source_type` | Meaning |
|---|---|
| `measured` | Computed from repo facts or tool output |
| `configured` | Read from `.pebra.yml` or explicit project policy |
| `elicited` | Produced by structured user/stakeholder elicitation |
| `estimated` | Inferred from features, heuristics, or model output |
| `derived` | Calculated from other score objects by a declared formula |
| `prior_uncalibrated` | Transparent startup prior before enough outcomes exist |

Use `provider` for the concrete source:

```text
radon | bandit | codegraph | architecture_map | graphify | legacy_codeindex | .pebra.yml | outcome_store | model | user | criticality_token_prior
```

Example:

```json
{
  "value": 0.18,
  "source_type": "measured",
  "provider": "sem",
  "confidence": 0.84
}
```

### 2.5 Confidence Bands

Default confidence bands are configurable:

| Band | Default Range | Meaning |
|---|---:|---|
| `low` | `< 0.50` | Do not edit yet; gather evidence or ask |
| `medium` | `0.50 to < 0.75` | Tighten scope, inspect/test or write new tests, then re-score |
| `high` | `>= 0.75` | Edit may proceed if gates pass |

### 2.6 Calibration Status

Probability scores should declare calibration status. This applies to `p_success` and `p_event_j`.

| `calibration_status` | Meaning |
|---|---|
| `fitted_calibrated` | Calibrated against outcome history |
| `estimated_uncalibrated` | Estimated from features or model output before enough outcomes exist |
| `prior_uncalibrated` | Transparent startup prior by action/repo/event class |

### 2.7 Criticality Stages

Criticality is reported as an ordinal C-stage and resolved to a cardinal value only when math requires it.

| Stage | Cardinal Value | Meaning |
|---|---:|---|
| `C0` | 0.10 | Negligible consequence |
| `C1` | 0.30 | Low consequence |
| `C2` | 0.50 | Moderate consequence |
| `C3` | 0.80 | High consequence |
| `C4` | 1.00 | Catastrophic or irreversible consequence |

Raw stages must never be multiplied directly. Use the mapped cardinal value only as a disutility floor or threshold modifier.

### 2.8 Score Levels

Level 1 scores are raw evidence or direct estimates:

| Level 1 Score | Meaning |
|---|---|
| `benefit` | User value if the action succeeds |
| `p_success` | Probability target for successful action outcome |
| `blast_radius` | Scope of possible damage if wrong |
| `criticality` | Business/safety importance of touched code |
| `reversibility` | Ease of undoing the action |
| `testability` | How directly the action can be verified |
| `evidence_quality` | Strength and relevance of supporting evidence |
| `source_reliability` | Trustworthiness of evidence source |
| `scope_control` | How narrow and bounded the edit is |
| `review_cost` | Human review burden |
| `structural_signals` | Measured repo facts such as LOC, imports, complexity, churn, coverage, SAST |
| `event_probabilities` | Adverse-event probabilities |
| `event_disutilities` | Severity of adverse events |
| `uncertainty` | Variance, interval, or calibration uncertainty |

Level 2 scores are derived:

| Level 2 Score | Meaning |
|---|---|
| `expected_loss` | Sum of adverse-event probability times disutility |
| `expected_utility` | Expected benefit minus expected loss and review cost |
| `utility_sd` | Propagated uncertainty in expected utility |
| `risk_adjusted_utility` | Conservative lower-bound utility |
| `edit_confidence` | Controller score deciding whether the agent may edit now |

### 2.9 Human-Facing Labels

PEBRA's JSON schema should keep technical field names. Default CLI, MCP summaries, PR comments, and dashboards should use human-facing labels.

| Technical Term | Human Label | Plain Meaning |
|---|---|---|
| `recommended_decision` | Decision | What PEBRA recommends the agent should do next |
| `risk_budget_used` / `risk_budget_used_percent` | Risk Level | How close this edit is to the configured safety limit; canonical score is a ratio, rendered percent is `100 * risk_budget_used` |
| `expected_loss` | Expected Damage | Estimated harm burden if things go wrong |
| `risk_adjusted_utility` / `RAU` | Value After Risk | Whether the benefit still clears risk, review effort, and uncertainty |
| `edit_confidence` | Confidence | How sure PEBRA is that the agent has enough evidence to act |
| `blast_radius` | Affected Area | How much of the codebase could be impacted |
| `criticality` / `criticality_stage` | Code Sensitivity | How important or sensitive the touched code is |
| `p_success` | Chance of Success | Estimated chance the action solves the task |
| `p_event_j` | Failure Chance | Estimated chance of a specific adverse event |
| `event_disutilities` / `disutility_j` | Damage Severity | How bad that adverse event would be |
| `utility_sd` | Uncertainty Cushion | Extra caution subtracted because inputs are uncertain |
| `review_cost` | Review Effort | Human effort required to review the action safely |
| `reversibility` | Ease of Rollback | How easy it is to undo the action |
| `testability` | Test Coverage Fit | How directly the action can be checked |
| `source_reliability` | Evidence Reliability | How trustworthy the evidence source is |
| `scope_control` | Change Size Control | How narrow and bounded the edit is |

Default user-facing output should lead with:

```text
Decision
Risk Level
Code Sensitivity
Confidence
Value After Risk
Why
Required Guardrails
```

`Affected Area` is a measured fact, not a verdict bar. It belongs inside `Why` unless a detailed view is requested. Technical details such as raw RAU, expected-loss formulas, event probabilities, and provenance remain available in JSON or an explicit math/details view.

---

## 3. Inputs

PEBRA starts from a decision query. The query says what task is being attempted, which actions are being considered, what evidence already exists, and what evidence actions are allowed.

### 3.1 Canonical Request Schema

```json
{
  "schema_version": "0.1",
  "task": "Fix failing login validation",
  "intent": "bug_fix",
  "risk_policy_ref": ".pebra.yml",
  "known_constraints": [
    "keep the change localized",
    "do not upgrade dependencies unless required"
  ],
  "candidate_actions": [
    {
      "id": "a1",
      "label": "Patch validate_login only",
      "action_type": "edit",
      "intent": "Fix login validation with a targeted function patch.",
      "edit_type": "targeted_patch",
      "expected_files": ["src/auth.py", "tests/test_auth.py"],
      "requires_dependency_change": false,
      "requires_schema_change": false,
      "requires_network": false,
      "requires_migration": false,
      "writes_external_state": false,
      "rollback_plan": "git restore src/auth.py tests/test_auth.py",
      "test_plan": "run tests/test_auth.py"
    },
    {
      "id": "info_1",
      "label": "Inspect auth tests and local call sites",
      "action_type": "information",
      "information_type": "repo_inspection",
      "expected_files": ["src/auth.py", "tests/test_auth.py"],
      "expected_info_cost": 0.02,
      "expected_uncertainty_reduction": 0.18
    }
  ],
  "available_evidence": {
    "repo_inspected": false,
    "tests_found": false,
    "call_sites_checked": false,
    "official_docs_checked": false,
    "github_or_changelog_checked": false
  },
  "allowed_evidence_actions": [
    "inspect_repo",
    "run_tests",
    "check_official_docs",
    "check_github",
    "web_search",
    "ask_user"
  ],
  "missing_information_policy": "ask_or_inspect_before_scoring"
}
```

PEBRA should reject or downrank vague actions:

```text
Fix the auth module.
Refactor login.
Improve everything.
```

### 3.2 Question Layer

The question layer supplies judgment inputs, not statistical calibration.

| MC Input Type | Examples | Legitimate Source |
|---|---|---|
| Statistical / frequency inputs | `p_success`, `p_event_j`, regression probability | outcome store, calibration history, measured repo evidence |
| Judgment / preference inputs | `benefit`, `disutility_j`, `criticality`, `risk_tolerance`, `max_p_negative_utility` | question layer, `.pebra.yml`, MCDA, swing weighting, DCE/conjoint |
| Correlations | `blast_radius` with `p_success`, `review_cost` with file count | outcome store or explicitly configured project model |

Ad-hoc user questions can configure point values. Structured elicitation can configure distributions because it can capture value and uncertainty. Outcome data is required for fitted statistical distributions and correlations.

PEBRA may only create Monte Carlo distributions from declared query fields, measured repo evidence, configured policy, structured elicitation, or fitted outcome history. Free-text model guesses may explain uncertainty, but they must not create gate-driving distributions.

---

## 4. Evidence Discovery and Tools

PEBRA must not assign adverse-event probabilities from risk labels alone. Labels such as `migration_failure`, `dependency_break`, `public_api_break`, and `security_sensitive_change` are event classes, not evidence.

### 4.1 Evidence Pipeline

```text
decision query
  -> validate request schema
  -> classify action type
  -> gather allowed evidence
  -> load architecture map / anchors if available
  -> compute structural signals
  -> estimate p_success and adverse-event probabilities
  -> compute expected utility and RAU
  -> compute edit confidence
  -> apply gates
  -> return canonical response
```

### 4.2 Structural Signals

PEBRA should combine absolute thresholds with repo-relative percentiles.

| Signal | Why It Matters | Primary Method / Provider |
|---|---|---|
| File LOC / logical LOC | Monolith files are harder to understand and review | `radon`, `lizard`, `ast-metrics` |
| Module import fan-in | Many modules import this module | CodeGraph reverse edges |
| Module import fan-out | This file imports many modules | CodeGraph outgoing edges |
| Symbol fan-in | Many callers/references target a specific function/class/method | CodeGraph symbol graph + PEBRA percentile math |
| Architecture anchor / god node | The file or symbol is a stable domain anchor with high repo-relative fan-in or centrality | `ArchitectureKnowledgeProvider` over CodeGraph facts |
| Bridge centrality | The node connects multiple domains, so small changes can cross boundaries | cross-directory / cross-package edge proxy |
| Domain entrypoint | The node hosts a route, page, shell, grid, command, CLI, or public tool surface | architecture map, AST heuristics |
| Architecture domain ownership | The touched file belongs to a named domain such as spreadsheet/grid, auth, cache, payments, or plotting | coarse directory/package grouping |
| Dynamic imports | Runtime edges may be hidden | AST/string detection |
| Circular imports | Initialization/refactor fragility | strongly connected components |
| Function or module fan-in | More callers means broader breakage | call graph in-degree |
| Cyclomatic complexity | More paths require more tests | McCabe complexity |
| Maintainability Index | Composite structural health | `radon`, `ast-metrics` |
| Git churn and bug density | Hotspot risk | git history |
| Test coverage of touched code | Missing tests raises uncertainty | coverage mapping |
| Public/exported API changes | Downstream break risk | AST export diff |
| Dependency or lockfile changes | Transitive break/security risk | package diff, advisories |
| Migration/schema changes | Data changes are hard to reverse | migration detection |
| Security-sensitive operations | Shell, SQL, secrets, crypto risk | `bandit`, Semgrep |

Default bands:

```text
file_size_risk:
  critical if file_loc > 3000 or file_loc_percentile >= 0.95
  high     if file_loc > 1000 or file_loc_percentile >= 0.90
  moderate if file_loc > 300  or file_loc_percentile >= 0.75
  low      otherwise

fan_in_risk:
  critical if exported_public_api or fan_in_percentile >= 0.95
  high     if fan_in_percentile >= 0.90
  moderate if fan_in_percentile >= 0.50
  low      otherwise

import_graph_risk:
  critical if import_cycle_touched or exported_symbol_import_percentile >= 0.95
  high     if module_import_fan_in_percentile >= 0.90 or third_party_import_changed
  moderate if module_import_fan_in_percentile >= 0.50 or dynamic_import_detected
  low      otherwise

cyclomatic_complexity_risk:
  low       1-10
  moderate 11-20
  high      21-50
  critical 50+
```

Absolute caller counts may be displayed in explanations, but high-risk escalation should use repo-relative fan-in percentiles, public/exported status, transitive reach to consequence-bearing symbols, or capability/side-effect evidence. A fixed threshold such as "3 callers" is not portable across a small repo and a monorepo.

### 4.3 Architecture Knowledge Layer

PEBRA uses CodeGraph as the required local precision graph engine. CodeGraph supplies multi-language symbols, edges, files, and index freshness. PEBRA builds its own small risk-relevant architecture summary from those facts, separate from the risk-decision ledger.

Architecture knowledge is **pre-decision evidence**:

```text
candidate edit
  -> ArchitectureKnowledgeProvider
      -> CodeGraph nodes / edges / files
      -> architecture_anchors
      -> architecture_domains
      -> graph_freshness
  -> structural risk signals
  -> event probabilities and review cost
  -> decision gates
```

This layer builds from CodeGraph's local SQLite index plus PEBRA's own deterministic interpretation:

- CodeGraph `nodes`, `edges`, `files`, and `project_metadata`.
- PEBRA-normalized symbols, directory/package boundaries, edge-confidence tiers, and entrypoint/criticality mapping.
- comparison artifacts such as `ARCHITECTURE.md`, `graphify-out/ANCHORS.md`, `graphify-out/graph.json`, legacy `codeindex`, or GitNexus reports only in benchmarks/research, not the production graph path.

CodeGraph is a runtime prerequisite for product graph evidence. Before PEBRA trusts graph evidence it runs `codegraph sync --quiet <repo>`, then `codegraph status --json <repo>`. Fresh graph evidence requires initialized status, zero pending added/modified/removed files, `index.reindexRecommended=false`, and no worktree mismatch. Otherwise graph evidence is stale and PEBRA fails closed or routes a would-be proceed to `inspect_first`.

It should produce:

```text
ArchitectureEvidence {
  graph_commit,
  graph_freshness,
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

`graph_freshness` has four states:

| State | Meaning |
|---|---|
| `fresh` | The content-hash cache matches the current repo files. |
| `rebuilt` | One or more files changed and PEBRA rebuilt the map successfully. Evidence is trustworthy. |
| `stale` | The graph rebuild failed; PEBRA cannot vouch for architecture evidence. |
| `unknown` | No graph exists or there is nothing to map. |

The purpose is not to replace blast radius or criticality. It separates two risk channels:

| Channel | Example | Feeds |
|---|---|---|
| Architecture centrality | `SpreadsheetView.tsx` is a high-degree, high-bridge domain anchor | `blast_radius`, `p_event`, review cost, `inspect_first` / `test_first` pressure |
| Domain criticality | account linking, auth tokens, payments, migrations | disutility floor, tighter thresholds, confirmation / `ask_human` pressure |

This distinction matters because a god node can be risky even if it is not security-sensitive, and a sensitive account-linking path can be risky even if it has low graph degree.

Baseline derivation:

| Derived Field | Default Method |
|---|---|
| `architecture_nodes` / `architecture_edges` | derived from CodeGraph nodes/edges and normalized by PEBRA |
| `god_node_score` | repo-relative fan-in percentile, floored below the architecture-anchor minimum |
| `architecture_anchors` | in-degree must meet both a minimum floor and a top fan-in percentile |
| `fan_out` | outgoing import count of edited files |
| `cycle_participation` | edited file participates in an import-cycle SCC |
| `bridge_centrality` | count or percentile of edges crossing top-level directory, package, or coarse-domain boundaries |
| `domain_entrypoint` | route/page/shell/grid/command/CLI/MCP/`main`/`run`/`handle_*` heuristics |
| `architecture_domains` | coarse top-level directory / package grouping |
| `domain_criticality_hint` | capability/path tokens such as auth, login, payment, billing, session, crypto, token, secret |

The architecture map should be persisted as rebuildable SQLite projections:

```text
architecture_nodes
architecture_edges
architecture_anchors
architecture_domains
```

These tables store codebase shape, not PEBRA decisions. They complement:

```text
assessments / outcomes / prediction_errors / learned_risk_facts / risk_snapshots
```

Freshness has two roles:

| Freshness Check | Purpose |
|---|---|
| assessment freshness | detects that one risk decision used stale evidence |
| architecture-map freshness | maintains or invalidates the reusable codebase map |

Architecture-map freshness is CodeGraph-status based, not commit based. PEBRA asks CodeGraph to sync, then trusts only a clean `codegraph status --json`: initialized, zero pending added/modified/removed files, `index.reindexRecommended=false`, and no worktree mismatch. `graph_commit` remains provenance only.

If CodeGraph is stale, unavailable, uninitialized, worktree-mismatched, or reindex-recommended, PEBRA must not treat graph evidence as fresh. The adapter should attempt `codegraph sync --quiet` first; if `codegraph status --json` is still not clean, graph evidence is stale and the evidence-validity gate routes a would-be proceed to `inspect_first` or fails closed for graph-required commands. Legacy external artifacts may be marked stale and ignored, but they do not replace CodeGraph as the production graph source.

The evidence-validity gate runs as the last check before `proceed`: if `graph_freshness=stale` and `inspect_on_stale_arch_map=true`, PEBRA returns `inspect_first`. The gate only downgrades an otherwise proceedable assessment; it never masks stricter risk gates and cannot be converted by a sanction.

Graph incompleteness is also assessment evidence. Missing expected files, parse-failed expected files, unresolved internal imports, dynamic imports, wildcard imports, and repo-wide dynamic/wildcard surfaces produce a bounded `graph_uncertainty_score` plus provenance lists. This lowers `evidence_quality` and therefore edit confidence. It must never inflate blast counts or expected loss by inventing dependents.

PEBRA must not become a graph platform. It owns the small risk-relevant architecture summary; heavy graph generation, visualization, embeddings, or Graphify-style full corpus indexing remain external tools or adapters.

### 4.4 Symbol-Level Change Classification

PEBRA must resolve risk at the edited-symbol level when enough local evidence exists. A sensitive file or god node is not uniformly dangerous: comments, formatting, and safe tests should not trigger controlled high-risk mode just because they live in a C4 path, while a one-character behavioral change inside a payment calculation may be catastrophic.

Symbol-level evidence is the canonical risk-resolution layer, not only a high-risk-mode nuisance filter. The scoring pipeline uses a fixed stack:

```text
Layer 0 raw evidence
  files, paths, criticality globs, blast graph, architecture anchors, tests, policy
Layer 1 symbol/scope resolution
  changed symbol, change_kind, visibility, fan-in percentile, side effects, fallback reason
Layer 2 scores
  p_event, p_success, expected_loss, risk_budget_used, RAU, confidence, review cost
Layer 3 gates
  proceed / inspect_first / test_first / ask_human / reject
Layer 4 risk annotations
  risk_mode, high_risk_triggers[], trigger_summary, suppression reasons
Layer 5 controls
  required checks, controlled-high-risk blueprint, sanction requirements
```

File-level criticality, blast, and god-node status feed Layer 1 first when symbol evidence exists. They do not directly inflate formulas. Layer 4 annotations are read-only renderings of Layer 0-3 results; they do not re-query evidence and do not create another decision path.

The v1 pipeline should produce:

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
  visibility,
  change_kind,
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

`SymbolDiffEvidence` is raw classified evidence. Final `high_risk_triggers[]` are assembled later by `core/decision_engine.py` / `core/high_risk_controls.py` from symbol evidence, scores, gates, policy, learned facts, and evidence gaps. Adapters must not emit finalized triggers.

Canonical `change_kind` values:

| Change Kind | Meaning |
|---|---|
| `COSMETIC` | whitespace, formatting, ordinary comments, ordinary docstrings |
| `DIRECTIVE` | comments/pragmas that affect behavior, type checking, linting, build, routing, or framework behavior |
| `TEST_ONLY` | test-only change with no production behavior or risky fixture/data mutation |
| `BEHAVIORAL` | function/method body logic changed, even if the diff is tiny |
| `CONTRACT` | signature, return shape, exported/public API, route/tool/schema, response shape, or consumer-visible behavior changed |
| `SIDE_EFFECT` | payment call, DB write, migration, deletion, external-state write, idempotency/retry/transaction boundary changed |
| `UNKNOWN` | parser, patch, or fan-in evidence unavailable; fall back conservatively |

High-risk mode should not trigger from file membership alone:

```text
C4 path alone                  is not sufficient
payment path alone             is not sufficient
god-node / architecture anchor is not sufficient
```

The trigger requires critical context plus a consequential symbol/change:

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

Diff size, line count, and number of changed symbols are not safety signals by themselves. A tiny change such as `amount * 100 -> amount * 1000` is `BEHAVIORAL`; a large formatting-only change is `COSMETIC`.

"Dead code" must not be used as a broad cosmetic exemption. Treat a symbol as low consequence only when it is private, not exported, not an entrypoint, has no dynamic-dispatch/reflection evidence, has no external consumers, and has no transitive path to a consequence-bearing symbol. Otherwise use `UNKNOWN` or `BEHAVIORAL`.

`pebra_verify` must rerun the full symbol classifier on the actual diff. A mismatch where the actual diff is more severe than the pre-edit proposed patch is scope drift and must route to reassessment or human review. Contract-surface scanning alone is insufficient because dangerous body changes may preserve the public signature.

### 4.5 Evidence Escalation Ladder

Use repo-local evidence first. Escalate only when local evidence is insufficient.

1. Local repo evidence: code, imports, tests, git history, call graph, dependency graph, architecture map, project config.
2. Official documentation: framework, language, library, or API docs for the detected version.
3. GitHub/source evidence: upstream repository, changelog, release notes, issues, examples, advisories.
4. Web search: only when local, docs, and source evidence are insufficient.
5. User question: when the missing evidence is project intent, domain risk, or risk tolerance.

Exception: ask the user earlier when the missing evidence is inherently project-specific, such as domain criticality, risk tolerance, or acceptance criteria.

External sources may inform behavior, API usage, edge cases, and failure modes. PEBRA must not copy external code verbatim into the local patch. The output should summarize extracted logic and cite provenance where available.

---

## 5. Scoring Dimensions

### 5.1 Benefit

Benefit estimates user value if the action works. It is not model confidence.

For v1:

```text
Benefit(a) = sum_k w_k * v_k(a)
```

Where:

- `v_k(a)` is the action score on criterion `k`.
- `w_k` comes from the weighting strategy in Section 6.
- Weights are normalized so `sum_k w_k = 1`.

AD-28 refines this scalar into a provenance-traced `benefit_breakdown`. The scalar `benefit` remains the gate-driving input to RAU, but it must be resolved from explicit value components rather than an opaque optimistic claim.

Canonical benefit components:

| Component | Meaning | Default treatment |
|---|---|---|
| `immediate_benefit` / `task_value` | Short-term value of satisfying the requested task | Positive value if the action succeeds |
| `maintainability_delta` | Long-term code-health change: simpler code, lower coupling, better testability, clearer architecture, or the reverse | Derived from proposed-patch metrics before edit; measured from actual diff after verify |
| `technical_debt_interest` | Future maintenance drag from shortcuts, duplicated logic, fragile workarounds, or missing tests | Derived/measured future cost from churn, complexity, coupling, testability, and affected scope |
| `durability` / `recurrence_risk` | Whether the fix is likely to stay fixed rather than require a revert, re-edit, or follow-up regression fix | Used to compute expected rework cost |
| `information_value` | Value of inspecting, testing, or gathering evidence before committing | Primarily attached to information actions such as `inspect_first` / `test_first` |
| `strategic_business_value` | Product, customer, deadline, compliance, or organizational value | Elicited/configured; never inferred only from code tokens |

Two-horizon v1 form:

```text
benefit =
  immediate_benefit
  + discounted_long_term_value

discounted_long_term_value =
  discount_factor * (
    future_maintenance_savings
    + information_value
    + strategic_business_value
    - technical_debt_interest
    - expected_rework_cost
  )

future_maintenance_savings =
  expected_future_change_exposure(scope) * maintenance_effort_delta_per_change

expected_rework_cost =
  recurrence_risk * rework_cost_per_recurrence
```

`rework_cost_per_recurrence` must be scaled to the affected scope's consequence, expected loss, or disutility scale, not to ordinary review cost. A recurrence in a C4 payment path should cost more than one additional review. If no calibrated recurrence-cost estimate exists, use a conservative project-configured prior and mark it `prior_uncalibrated`.

Maintainability is a first-class economic outcome, not an optional fuzzy bonus. For active code, future maintenance exposure is assumed unless the scope is proven dormant. PEBRA estimates exposure from churn, ownership/activity, roadmap/config hints, dependency/API centrality, and architecture reach. It estimates maintenance effort delta from Maintainability Index, complexity, coupling, duplication, testability, analyzability, modularity, public-surface complexity, and symbol/scope evidence.

Pre-edit maintainability deltas are `derived` when computed from a concrete proposed patch AST/diff, `projected` when only a strategy description exists, and `measured` after `pebra_verify` sees the actual diff. Unsupported future-value claims receive no gate-driving credit. Positive maintainability benefit follows confirm-before-credit: a proposed improvement can be shown in the comparison, but it receives gate-driving weight only when backed by concrete patch metrics, configured policy, or verified outcomes. Maintainability degradation, new debt interest, and recurrence/rework cost count immediately when detected.

#### 5.1.1 Benefit Delta Measurement

PEBRA measures benefit deltas by comparing the current repo state to the proposed or actual after-state on the touched scope.

```text
raw_delta_k = metric_after_k - metric_before_k
directional_delta_k =
  raw_delta_k        if higher_is_better(k)
  -raw_delta_k       if lower_is_better(k)

normalized_delta_k = normalize_to_minus_one_plus_one(directional_delta_k, metric_k)

benefit_delta_k =
  normalized_delta_k * future_change_exposure(scope)

maintenance_effort_delta_per_change =
  sum_k weight_k * benefit_delta_k
```

Pre-edit:

```text
before = assessed_commit
after  = proposed_patch / candidate action
source_type = derived
```

Post-edit:

```text
before = assessed_commit
after  = actual_diff seen by pebra_verify
source_type = measured
```

If no concrete patch is available:

```text
source_type = projected
gate_driving_credit = 0 unless configured or ratified
```

Benefit delta dimensions:

| Delta | Direction | Measurement |
|---|---|---|
| `complexity_delta` | lower is better | Cyclomatic/cognitive complexity, nesting depth, Halstead, LOC per touched symbol/file |
| `modularity_delta` | higher is better | Fewer cross-module responsibilities, cleaner layer boundaries, fewer boundary violations |
| `coupling_delta` | lower is better | Fan-in/fan-out, imports, call-graph edges, cross-package edges |
| `cohesion_delta` | higher is better | Related code becomes more localized; fewer mixed-responsibility symbols/modules |
| `testability_delta` | higher is better | Tests added/updated, direct test coverage, fewer hidden dependencies, more pure/deterministic functions |
| `analyzability_delta` | higher is better | Smaller local reasoning scope, simpler control flow, clearer names, fewer hidden side effects |
| `modifiability_delta` | higher is better | Future changes require fewer files, call sites, side effects, and contract updates |
| `duplication_delta` | lower is better | Duplicate blocks, repeated logic, repeated schema/validation rules |
| `encapsulation_delta` | higher is better | Fewer leaked internals, stronger module boundaries, lower public mutable state |
| `api_surface_delta` | lower/stable is better | Public signatures, routes, schemas, exported symbols, and tool contracts added/removed/changed |
| `reusability_delta` | higher is better only when it reduces future change effort | Shared components or helpers without speculative abstraction |
| `portability_delta` | higher is better | Lower environment/runtime/platform coupling |
| `observability_delta` | higher is better | Better logging, tracing, metrics, diagnostics, and error messages |
| `operability_delta` | higher is better | Safer deploy, config, migration, rollback, feature-flag, idempotency, or retry behavior |
| `recurrence_delta` | lower is better | Revert/re-edit/reopened issue/follow-up regression probability |

Future exposure weights benefit by how often and how widely the touched scope is expected to matter:

```text
future_change_exposure(scope) =
  f(recent_churn,
    ownership_activity,
    repo/domain activity,
    roadmap/configured active areas,
    callers_percentile,
    public_api_or_exported_symbol,
    criticality_stage,
    incident_or_rework_history)
```

Do not reward "more architecture" by default. New abstractions only score positive when they reduce future change effort through lower coupling, higher cohesion, lower duplication, better testability, stable contracts, or lower recurrence risk. Otherwise they may be negative because they add indirection, review cost, blast radius, or public surface.

Net-benefit style ranking is the primary comparison lens:

```text
net_benefit_score = benefit - expected_loss - review_cost
```

`risk_adjusted_utility` remains the decision score because it subtracts the uncertainty cushion. ICER-style pairwise ratios may be shown as diagnostics, but they are not the primary ranking metric because ratios become unstable when incremental benefit is near zero.

Double-counting guard:

- `p_success` is the probability of solving the task; `benefit` is how much success is worth.
- `criticality` and `disutility_j` are downside if failure occurs; `strategic_business_value` is upside if success occurs.
- `expected_loss` is downside from adverse events in this edit; `expected_rework_cost` is future cost from recurrence or re-edit after a superficially successful change.
- `recurrence_avoidance_value` may be reported in an alternative comparison as avoided rework relative to another candidate, but it must not be added on top of the same candidate's `expected_rework_cost`.
- Coupling/fan-in may feed risk through affected area; only the delta caused by the candidate action may feed maintainability value.
- `review_cost` is immediate human effort; `technical_debt_interest` is future maintenance drag.

PEBRA ranks alternatives the agent or caller proposes. It may render a qualitative `better_value_alternative` for a single-action `pebra_assess`, but quantified incremental comparison requires multiple candidate actions in `pebra_compare`.

### 5.2 P(success)

`p_success` is a probability target. It should become calibrated over outcomes, but cold-start v1 may use transparent uncalibrated estimates.

Allowed `p_success` provenance:

| Source | Meaning |
|---|---|
| `fitted_calibrated` | Model calibrated against outcome history |
| `estimated_uncalibrated` | Feature/model estimate before enough outcomes exist |
| `prior_uncalibrated` | Transparent startup prior by action/repo class |

Do not call cold-start estimates calibrated.

Calibration target:

```text
When PEBRA says p_success = 0.70, roughly 70% of those actions should succeed over time.
```

### 5.3 Blast Radius

Blast radius is a structural input to event probabilities. It is not itself harm.

For v1, use measured impact scores from providers such as `sem`, import graph adapters, or structural metrics. For v2, graph propagation may use weighted dependency influence.

### 5.4 Criticality

Criticality estimates consequence if the touched code fails. It is not the same as usage count.

PEBRA should split criticality into three axes:

```text
security_criticality_stage = C0-C4
correctness_safety_criticality_stage = C0-C4
business_criticality_stage = C0-C4

criticality_stage =
  max(
    security_criticality_stage,
    correctness_safety_criticality_stage,
    business_criticality_stage
  )

criticality_value = STAGE_MAP[criticality_stage]
```

Each sub-axis must be scored on the same C0-C4 scale before the outer `max()` is applied.

Security criticality is the adversarial side: auth bypass, privilege escalation, injection, exfiltration, unsafe crypto, exposed secrets, and similar attack-enabling behavior.

Correctness/safety criticality is the non-adversarial side: wrong payment amount, tax/currency error, data corruption, idempotency break, failed reconciliation, irreversible migration, or destructive data operation.

Business criticality is the project-specific value side: checkout may matter more than settings, payments may matter more than marketing pages, and medical/safety workflows may require special gates.

### 5.4.1 Criticality Staging Scale

PEBRA should use a local software consequence scale:

| Stage | Cardinal Value | Meaning | Examples | Gate Pressure |
|---|---:|---|---|---|
| `C0` | 0.10 | Negligible consequence | docs, comments, formatting | no extra pressure |
| `C1` | 0.30 | Local annoyance, easy rollback | UI copy, styling, low-stakes display | normal gates |
| `C2` | 0.50 | Feature degradation | search, reports, dashboard display | prefer targeted tests |
| `C3` | 0.80 | Business, security, or user-impacting failure | auth, admin, billing, PII, external state writes | tighter thresholds and confirmation |
| `C4` | 1.00 | Catastrophic or irreversible consequence | money movement, data deletion, secret leak, destructive migration, safety control | human gate by default |

This table is descriptive. Section 8 owns the actual decision gates. This is a software analogue of medical staging: observable criteria map to a consequence class, and outcome history later calibrates the mapping. Unlike medicine, software has no universal "death" outcome; catastrophic consequence must be declared per project.

### 5.4.2 Capability-Based Criticality Detection

PEBRA should not rely on scary words alone. Tokens and paths are weak priors. Capabilities are stronger evidence.

| Capability | Evidence | Suggested Axis |
|---|---|---|
| Payment movement | Stripe/PayPal/payment SDK imports, charge/refund APIs | correctness + business |
| Authentication/session | login, password, session, token, OAuth/JWT code | security |
| Authorization/admin | role checks, permission logic, admin routes | security + business |
| Data deletion | SQL `DELETE`, destructive ORM calls, file deletion | correctness/safety |
| PII/secrets | email/address/credential storage, secret access | security + business |
| Crypto/security boundary | encryption, signing, hashing, TLS config | security |
| External state write | DB writes, queues, webhooks, third-party APIs | correctness/safety |
| Migration/schema | migration files, DDL, irreversible transforms | correctness/safety |
| Dependency supply chain | package/lockfile change, semver major, advisories | security + correctness |

Token/path matches may nominate criticality:

```text
payment, refund, charge, admin, password, token, secret, delete, migration
```

But token matches must be stored as weak evidence:

```json
{
  "source_type": "estimated",
  "provider": "criticality_token_prior",
  "confidence": 0.35
}
```

They must not directly assign final criticality without capability evidence or policy confirmation.

### 5.4.3 Security Taxonomy Mapping

Security frameworks provide citable vocabulary for the adversarial axis:

- OWASP Risk Rating: likelihood, technical impact, and business impact.
- CVSS: base, threat, environmental, and supplemental metrics.
- CISA SSVC: decision-oriented prioritization with exploitation, technical impact, automatable, mission prevalence, and public-wellbeing considerations.
- MITRE CWE/CAPEC/ATT&CK: weakness, attack-pattern, and adversary-behavior vocabulary.
- STRIDE: spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege.

PEBRA may map detected capabilities to those categories, but security taxonomies do not cover all criticality. Payment correctness, tax logic, reconciliation, and data integrity bugs can be catastrophic without being attacks.

### 5.4.4 Project Override and Outcome Calibration

Project policy remains the final value layer:

```yaml
criticality:
  "src/payments/**": C4
  "src/auth/**": C3
  "src/migrations/**": C4
  "src/docs/**": C0
```

For actions touching multiple files:

```text
criticality(action) = max(criticality(file) for file in expected_files)
```

Use max aggregation because a single critical file can dominate disutility floors and threshold tightening. This is a conservative file-level fallback/floor, not a high-risk trigger by itself and not a `p_event` input when symbol-level evidence exists. If symbol/scope evidence is available, the file-level C-stage tells PEBRA where to inspect harder and how severe consequence-bearing failures could be; the edited symbol, change kind, fan-in/exportedness, and side-effect flags decide likelihood and controlled-high-risk routing.

Over time, PEBRA can calibrate criticality against incidents and regressions. Prefer odds-ratio or logistic calibration before survival/Cox models because most projects have limited incident counts:

```text
severe_incident ~ capability_flags + path_criticality + edit_type + blast_radius
```

This can later produce evidence such as these illustrative, non-computed examples:

```text
payment_change: 3.5x higher odds of severe incident
migration_change: 4.2x higher odds
auth_change: 2.8x higher odds
```

Survival/hazard-ratio modeling belongs in v2 research unless PEBRA has enough time-to-incident data and the assumptions are checked.

### 5.5 Adverse Event Model

Expected loss uses adverse-event probabilities and disutilities:

```text
p_event_j = event_model_j(features)
d_prior = STAGE_MAP[criticality_stage]

# Event-class-aware floor: the criticality floor applies ONLY to
# consequence-bearing events. Incidental events (test_regression,
# review_burden) keep their elicited disutility — they are never floored
# just because the touched path is critical.
if event_j in CONSEQUENCE_BEARING_EVENTS:
    disutility_j = max(elicited_disutility_j, d_prior)
else:
    disutility_j = elicited_disutility_j

expected_loss(a) = sum_j p_event_j(a) * disutility_j

CONSEQUENCE_BEARING_EVENTS = {
    public_api_break, security_sensitive_change,
    external_state_damage, migration_failure, dependency_break,
    api_contract_break, route_behavior_break,
    tool_schema_break, response_shape_mismatch,
    consumer_shape_mismatch
}
```

The criticality stage supplies a disutility floor, not a multiplier, and that floor applies only to the consequence-bearing events listed above. `p_event_j` remains the likelihood channel and should be driven by codebase evidence such as usage counts, blast radius, tests, changed APIs, and structural signals. The raw C-stage is never multiplied, and incidental events such as `test_regression` are never floored by criticality.

Each event loss component should preserve criticality provenance:

```json
{
  "criticality_stage": "C4",
  "criticality_value": 1.0,
  "disutility_method": "max(elicited_disutility, criticality_floor)",
  "floor_applied": true
}
```

Cold-start event probabilities use transparent priors:

```text
p_event_j = prior_uncalibrated_j(action_class, repo_class, evidence_flags)
```

Do not label a probability source calibrated until calibration has been checked against outcome data.

Default event classes:

| Event | Probability Features | Disutility Source |
|---|---|---|
| `test_regression` | blast radius, touched tests, coverage, churn, complexity | MCDA elicitation |
| `public_api_break` | exported symbol changed, import fan-in, dependency depth, dependent tests | MCDA elicitation |
| `api_contract_break` | API handler changed, route map changed, consumer count, contract tests | MCDA elicitation |
| `route_behavior_break` | route handler changed, middleware/auth behavior changed, dependent callers | MCDA elicitation |
| `tool_schema_break` | MCP/RPC tool schema changed, handler signature changed, agent/tool consumers | MCDA elicitation |
| `response_shape_mismatch` | response keys changed, serializer changed, consumer property access mismatch | MCDA elicitation |
| `consumer_shape_mismatch` | consumer expects missing/renamed fields, typed contract mismatch, shape check findings | MCDA elicitation |
| `migration_failure` | migration flag, schema change, rollback plan, migration history | MCDA elicitation |
| `dependency_break` | dependency change, lockfile size, semver level, changelog/advisory signals | MCDA elicitation |
| `external_state_damage` | network use, DB writes, filesystem writes, external API writes | MCDA elicitation |
| `security_sensitive_change` | critical path, SAST findings, secret/crypto/shell/SQL patterns | MCDA elicitation |

Review burden is not an adverse event by default. It is subtracted separately as `review_cost`. Only model it as an adverse event if there is a separate downstream failure, such as review delay causing missed release risk.

Contract-surface events are consequence-bearing because a small edit can break downstream callers even when local tests pass. They use the same criticality floor as other consequence-bearing events, but their probabilities must be driven by measured contract evidence: exported symbols, API routes, tool schemas, response shapes, consumer property access, and dependent tests. They should not be inferred from labels alone.

### 5.6 Review Cost

`review_cost` estimates human effort required to review safely.

Features:

- Files touched.
- Expected diff size.
- Conceptual spread.
- Complexity.
- Churn.
- Public API surface.
- Dependency/migration impact.

### 5.7 Evidence Aggregation

When multiple independent estimates exist:

```text
w_i = (1 / variance_i) / sum_j(1 / variance_j)
pooled_estimate = sum_i w_i * estimate_i
pooled_variance = 1 / sum_i(1 / variance_i)
```

If evidence sources are correlated, PEBRA should model covariance, keep the most reliable source, or mark the combined estimate as conservative.

### 5.8 Uncertainty

PEBRA should store uncertainty as variance, confidence interval, calibration error, or scenario interval wherever possible.

Trigger uncertainty when:

- Candidate action is vague.
- Expected files are unknown.
- Tests are missing.
- Blast-radius provider fails.
- Calibration data does not match repo/language/task type.
- External docs or dependency behavior are version-uncertain.

---

## 6. Weighting Strategy

PEBRA uses MCDA methods only to make weights, preferences, uncertainty, or rank stability more defensible. It must not become a generic MCDA method catalogue.

### 6.1 Weight Provenance Ladder

```text
1. fitted_outcome_weights
2. elicited_or_configured_weights
3. objective_weights
4. rank_surrogate_weights
5. equal_weights
```

Each fallback must explain why higher-quality sources were unavailable:

```json
{
  "weight_source": "elicited_bwm",
  "fallback_reason": "no fitted outcome weights available",
  "consistency_check": "passed",
  "method_provenance": "configured project elicitation"
}
```

| Rung | Use | Guard |
|---|---|---|
| `fitted_outcome_weights` | Learned from recorded outcomes | Requires enough calibrated local or benchmark outcome data |
| `elicited_or_configured_weights` | Human/project risk preferences | Requires consistency checks or explicit policy provenance |
| `objective_weights` | Data-derived weights such as CRITIC/Entropy | Requires enough candidate actions and stable criterion variance |
| `rank_surrogate_weights` | ROC/rank-order weights from a priority list | Use when stakeholders can rank criteria but not score comparisons |
| `equal_weights` | Last-resort neutral fallback | Must be labeled fallback |

Small-n guard:

```text
if candidate_action_count < 4:
  do not use candidate-set objective weighting as a gate-driving source
  fall back to fitted, elicited/configured, rank-surrogate, or equal weights
```

### 6.2 Structured Elicitation

For judgment inputs such as `benefit`, `disutility_j`, `criticality`, and risk tolerance, structured elicitation is preferred over free text.

Usable methods:

- AHP with consistency checks.
- BWM / simplified BWM for fewer comparisons.
- SMART/SMARTER.
- Swing weighting.
- DCE/conjoint for later richer elicitation.

Rule:

```text
if elicited_weight_consistency fails:
  ask_user_to_revise_preferences
  do not promote weights to gate-driving provenance
```

---

## 7. Decision Math

### 7.1 Expected Utility and RAU

```text
benefit         = resolve_benefit(benefit_breakdown)
expected_benefit = p_success * benefit
expected_loss    = sum_j p_event_j * disutility_j

expected_utility =
  expected_benefit
  - expected_loss
  - review_cost
```

Risk adjustment uses a lower confidence bound:

```text
risk_adjusted_utility =
  E[utility] - z_alpha * SD(utility)
```

Default interpretation:

```text
RAU >  0.00   proceed candidate, subject to gates
RAU =  0.00   break-even
RAU <  0.00   reject, inspect, test, or ask
```

Do not label raw RAU as a percentage.

### 7.2 SD(utility)

Default v1 method is first-order error propagation:

```text
U = p_success * benefit - sum_j(p_event_j * disutility_j) - review_cost

Var(U) =
  benefit^2 * Var(p_success)
  + p_success^2 * Var(benefit)
  + Var(review_cost)
  + sum_j[
      disutility_j^2 * Var(p_event_j)
      + p_event_j^2 * Var(disutility_j)
    ]
  + scenario_variance

SD(utility) = sqrt(Var(U))
```

This default assumes independent inputs unless covariance terms are explicitly added. It can understate or overstate uncertainty when inputs are correlated.

`Var(benefit)` includes uncertainty from benefit components. Cold-start or uncalibrated long-term value claims must widen `Var(benefit)` and therefore lower RAU through the uncertainty penalty; they must not inflate RAU without a matching uncertainty cost. Projected components without concrete patch metrics use at least `learning.projected_benefit_variance_floor` and receive zero gate-driving positive credit when `learning.projected_benefit_zero_gate_credit_without_evidence=true`.

Maintainability metrics derived from a concrete proposed patch are not free-text uncertainty; they are deterministic projections from code evidence. Their variance should reflect measurement coverage and missing evidence, not a general assumption that maintainability is unknowable.

Each input's variance is resolved in this precedence order:

```text
1. Explicit variance, if supplied with the input (as in the Section 10 worked example).
2. Derived from the input's confidence:
       Var(x) = ((1 - confidence_x) / 2) ** 2
   (confidence 1.0 -> 0.0, 0.5 -> 0.0625, 0.0 -> 0.25)
3. Cold-start default (prior_uncalibrated) when neither is available:
       Var(p_success)=0.04, Var(benefit)=0.01,
       Var(p_event_j)=0.0025, Var(disutility_j)=0.0025,
       Var(review_cost)=0.01, scenario_variance=0.0003
```

The Section 10 worked example supplies explicit variances (precedence 1); its variance_breakdown sums to 0.0036, giving SD = sqrt(0.0036) = 0.06. The confidence-derived mapping and cold-start defaults are fallbacks and are not expected to reproduce that exact SD.

### 7.3 Monte Carlo RAU

Monte Carlo gates activate only when PEBRA has defensible distributions and correlation assumptions.

```text
monte_carlo_gate_available =
  distribution_source in {"fitted", "configured"}
  and correlation_source in {"fitted", "configured", "independent_assumption"}
  and sample_count >= min_monte_carlo_sample_count
```

If `distribution_source` or `correlation_source` is only `assumed`, Monte Carlo results may be reported as exploratory diagnostics but must not drive hard gates.

Sampling:

```text
sample p_success, benefit, p_event_j, disutility_j, review_cost
compute U for each sample
utility_sd = standard_deviation(U_samples)
RAU_alpha = percentile(U_samples, alpha)
P(utility < 0) = fraction(U_samples < 0)
P(action is best) = fraction(action has max utility across paired samples)
```

Monte Carlo output must report:

```text
distribution_source: fitted | configured | assumed
correlation_source: fitted | configured | independent_assumption | assumed
sample_count: integer
```

Configured triangular ranges may be used for judgment inputs:

```text
benefit = triangular(low=0.70, mode=0.82, high=0.90)
disutility(public_api_break) = triangular(low=0.60, mode=0.80, high=0.95)
distribution_source = configured
```

This is not appropriate for `p_success` or `p_event_j` unless those ranges come from fitted outcome history or explicit calibrated evidence.

Criticality stages can configure disutility uncertainty for Monte Carlo:

```text
if criticality_stage == C3:
  disutility_j_sample ~ triangular(0.65, 0.80, 0.92)

if criticality_stage == C4:
  disutility_j_sample ~ triangular(0.85, 1.00, 1.00)
```

Samples use mapped cardinal disutility values, not raw ordinal stages.

Configured correlations may be supplied offline through structured expert influence mapping:

```text
blast_radius -> p_success: negative influence
blast_radius -> review_cost: positive influence
criticality_stage -> disutility_floor: positive influence
correlation_source = configured
```

These are configured assumptions, not fitted evidence.

### 7.4 Edit Confidence

RAU answers: is the action worth doing under risk?

Edit confidence answers: does the agent have enough evidence to perform this edit now?

Use a weighted geometric mean:

```text
edit_confidence =
  exp(sum_i w_i * ln(x_i))

x_i in {
  calibrated_or_estimated_p_success,
  evidence_quality,
  testability,
  reversibility,
  source_reliability,
  scope_control
}

sum_i w_i = 1
```

Default weights are equal:

```yaml
edit_confidence_weights:
  p_success: 1/6
  evidence_quality: 1/6
  testability: 1/6
  reversibility: 1/6
  source_reliability: 1/6
  scope_control: 1/6
```

The implementation should parse fractional weights or normalize configured numeric weights so `sum_i w_i = 1`. The implementation may override these through `.pebra.yml` if provenance is stored.

---

## 8. Decision Gates and Outcomes

### 8.1 Confidence State Machine

```text
if confidence_band == high:
  proceed with smallest sufficient edit if gates pass

if confidence_band == medium:
  gather cheap local evidence
  run targeted tests or static checks
  re-score
  proceed only if confidence improves and residual risk is low

if confidence_band == low:
  do not edit yet
  gather repo, docs, GitHub/source, web, or user evidence as needed
  re-score
  if confidence improves, present evidence_delta
  proceed only with confirmation or explicit project policy
```

Confidence upgrades require evidence:

```text
confidence_upgrade_allowed only if:
  new evidence was gathered
  evidence source is reliable
  evidence matches current repo, dependency version, or runtime
  original uncertainty source was reduced
  remaining risks are stated
```

If the only new evidence is retrieval from docs, GitHub, or web search, cap the upgraded confidence:

```text
if confidence_upgrade_source == retrieval_only:
  edit_confidence = min(edit_confidence, thresholds.max_retrieval_only_confidence)
```

### 8.2 Hard Gates

Gate names must map directly to `.pebra.yml`.

The first matching risk gate sets a provisional decision. Sanction resolution runs after that provisional decision and may finalize a risk-threshold `ask_human` / `reject` into controlled-high-risk `proceed` only when the sanction is valid and required controls are satisfied.

```text
if action violates policy:
    reject

if criticality_stage == C4
and thresholds.c4_always_ask_human
and symbol_diff_requires_c4_gate:
    requires_confirmation = thresholds.c4_requires_confirmation
    ask_human

if criticality_stage == C4
and verified_change_kind in {COSMETIC, TEST_ONLY}
and not consequential_symbol_changed:
    do not trigger controlled-high-risk mode from C4 membership alone

if criticality_stage in {C3, C4}:
    max_expected_loss_limit = min(
      thresholds.max_expected_loss_without_human,
      thresholds.<stage>_max_expected_loss_without_human
    )
    requires_confirmation = thresholds.<stage>_requires_confirmation
else:
    max_expected_loss_limit = thresholds.max_expected_loss_without_human

if expected_loss > max_expected_loss_limit:
    ask_human or reject

if risk_adjusted_utility < 0:
    reject                                    # default (AD-2)
    # ask_human instead, if thresholds.ask_on_negative_rau is set

if monte_carlo_gate_available            # v1.5 gate; v1 skips when unavailable
and P(utility < 0) > thresholds.max_p_negative_utility:
    ask_human or reject

if not monte_carlo_gate_available
and utility_sd > thresholds.max_utility_sd_without_human
and expected_utility > 0:
    ask_human

if monte_carlo_gate_available            # v1.5 gate; v1 uses §8.4 rank/interval fallback
and decision_instability > thresholds.decision_instability_threshold:
    inspect_first or test_first

if edit_confidence < thresholds.low_edit_confidence:
    inspect_first, test_first, ask_human, or reject

if confidence_upgrade_requested
and no evidence_delta exists:
    reject

if low_confidence_upgraded
and thresholds.require_user_confirmation_for_low_confidence_upgrade:
    proceed only with requires_confirmation = true

if authorized_sanction exists
and prior gate result is ask_human or reject
and rejecting gate in {C4 escalation, expected_loss threshold, RAU default reject, Monte Carlo negative-utility}
and pre_edit_authorization_controls are satisfied:
    proceed with risk_mode = controlled_high_risk
    requires_confirmation = true
    preserve original scores and high_risk_triggers

else:
    proceed
```

Criticality affects gates only through this section. Section 5 may describe gate pressure, but Section 8 is the sole decision authority.

High-risk routing is never a bare decision. If a high-risk condition causes `test_first`, `ask_human`, `reject`, or `risk_mode=controlled_high_risk`, the response must include `high_risk_triggers[]` and either a mapped control blueprint or a suppression reason. The decision enum remains exactly five values; trigger flags are explanatory and auditable companion evidence.

Sanction resolution is part of the gate sequence, not a post-hoc override. It may override risk-threshold gates only after authorized risk acceptance and verified controls. It must not silently override hard policy violations; policy exceptions require a distinct higher-scrutiny sanction type. If `pebra_verify` later detects stale evidence, scope drift, missing controls, or a more severe actual symbol diff, it invalidates the sanction and routes back through this gate sequence.

`symbol_diff_requires_c4_gate` defaults to true when symbol-diff evidence is unavailable (`UNKNOWN`) and false only when local classification verifies a non-consequential `COSMETIC` or safe `TEST_ONLY` change. This prevents nuisance triggers without letting parser failures suppress safety gates.

Monte Carlo gates are v1.5 behavior unless distributions and correlation provenance are fitted or explicitly configured. In v1, PEBRA reports Monte Carlo examples only as diagnostics and uses the non-MC rank-gap / interval-overlap fallback in §8.4 for borderline action ordering.

Double-count guard:

```text
criticality_stage -> disutility floor and threshold modifiers
symbol/scope evidence + count/blast_radius/usage -> p_event

Do not feed criticality_stage directly into p_event.
Do not multiply raw C-stage values.
```

### 8.3 Information Actions

Information actions gather evidence before edit actions.

Examples:

- Inspect the failing test.
- Search call sites.
- Run a targeted test.
- Check official docs or changelog.
- Ask user to choose between interpretations.

Information actions use:

```text
information_value =
  expected_uncertainty_reduction
  - info_cost
  - info_delay_cost
```

They do not use adverse-event expected loss unless the action changes code, data, or external state.

If an information action is recommended, the top-level response uses:

```json
{
  "decision": "inspect_first",
  "recommended_action_id": "info_1"
}
```

After the information action completes, the agent calls PEBRA again with updated `available_evidence`.

### 8.4 Decision Instability

With Monte Carlo:

```text
decision_instability = 1 - P(current_top_action is best)
```

Without Monte Carlo:

```text
if top_action_RAU_interval overlaps second_action_RAU_interval:
    inspect_first or test_first
```

Rank-gap fallback:

```text
acceptable_advantage_gap = 1 / (candidate_action_count - 1)

if top_action_score - second_action_score < acceptable_advantage_gap:
    inspect_first or test_first
```

Monte Carlo replaces interval-overlap or rank-gap heuristics when its distribution provenance is `fitted` or explicitly `configured`.

---

## 9. Output Schema

### 9.1 Canonical Response Schema

```json
{
  "schema_version": "0.1",
  "task": "Fix failing login validation",
  "repo_id": "repo_local_123",
  "repo_root": "/abs/path/to/repo",
  "assessed_commit": "abc123",
  "risk_snapshot_id": "R0",
  "prediction_error_model_id": "E0",
  "recommended_decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
  "risk_mode": "normal",
  "high_risk_triggers": [],
  "decision_reason": "Patch action has positive RAU after evidence, but confidence upgraded from low so confirmation is required.",
  "risk_report": {},
  "model_guidance_packet": {},
  "actions": [],
  "thresholds_used": {},
  "evidence_delta": {},
  "provenance": {}
}
```

Each action object should include its own per-action verdict:

```json
{
  "id": "a1",
  "label": "Patch validate_login only",
  "action_type": "edit",
  "action_status": "pending",
  "decision": "proceed",
  "risk_mode": "normal",
  "high_risk_triggers": [],
  "scores": {},
  "edit_control": {}
}
```

`recommended_decision` is the top-level decision for the selected action. Per-action `decision` records how each candidate was classified during comparison.

`risk_mode` is a companion field, not a sixth decision. Allowed values are `normal`, `sensitive_context`, `elevated_review`, and `controlled_high_risk`. High-risk routes must not be emitted as bare `ask_human` or bare `reject`: when high-risk conditions drive the route, the response must include `high_risk_triggers[]` explaining what fired, what evidence supported it, and which controls would be required to proceed. If `risk_mode=controlled_high_risk`, `high_risk_triggers[]` must be non-empty.

The response should also include `model_guidance_packet`: a deterministic, model-facing rendering of the same decision envelope. It tells the editing model the safe scope, which risky changes would invalidate the assessment, which checks are required, and which risk facts explain the instruction. It does not let the model reinterpret PEBRA's risk score.

Every metric is an object:

```json
{
  "value": 0.82,
  "level": "level_1",
  "source_type": "elicited",
  "provider": "user",
  "confidence": 0.70,
  "evidence": ["Directly addresses the failing login-validation task."],
  "method": "MCDA value function with normalized criterion weights"
}
```

### 9.2 Risk Report

Every PEBRA assessment should include a `risk_report` view object. It is derived from canonical scores and gates at render time; it should not be manually maintained as a separate source of truth.

The report has one headline risk number:

```text
if monte_carlo_gate_available:
  headline_risk_type = probability
  headline_risk_percent = 100 * P(utility < 0)
else:
  headline_risk_type = risk_budget_indicator
  headline_risk_percent = 100 * expected_loss / effective_expected_loss_threshold
```

`expected_loss` is shown as the raw score behind the budget. Do not render raw `expected_loss` as the headline percent because it can exceed 1.0. If the budget percent exceeds 100%, that is meaningful: the action is over the configured risk budget and should trigger the relevant Section 8 gate.

The denominator must be the effective threshold used by the gate, not always the global threshold. For example, C3 code uses `c3_max_expected_loss_without_human` when it is tighter than `max_expected_loss_without_human`.

RAU remains a signed decision score, not a risk percent. The risk report may show an RAU band from `.pebra.yml`:

```text
RAU < reject_below              -> negative
reject_below to borderline_below -> borderline
borderline_below to strong_at    -> proceedable
>= strong_at                     -> strong
```

The `why` field should be generated from existing evidence:

- Top adverse-event drivers ranked by `p_event_j * disutility_j`.
- The effective threshold and gate that applied.
- RAU waterfall: benefit, loss, review cost, uncertainty penalty.
- Criticality stage and provenance.
- Symbol/scope evidence: changed symbol, change kind, visibility, fan-in percentile, side-effect flags, and fallback reason.
- Weakest edit-confidence factor.
- High-risk trigger flags when the route is high-risk, including mapped controls or suppression reasons.

Example shape:

```json
{
  "risk_type": "risk_budget_indicator",
  "headline_risk_percent": 50,
  "expected_loss": {
    "value": 0.10,
    "source_type": "derived",
    "provider": "pebra"
  },
  "risk_budget_used_percent": 50,
  "budget_threshold_used": {
    "key": "c3_max_expected_loss_without_human",
    "value": 0.20,
    "reason": "Auth code is C3 and the C3 threshold is tighter than the global threshold."
  },
  "symbol_scope_evidence": {
    "scope_basis": "symbol",
    "changed_symbols": ["src/auth.py::validate_login"],
    "max_change_kind": "BEHAVIORAL",
    "visibility": "internal",
    "symbol_fan_in_percentile": 0.42,
    "consequential_symbol_changed": false,
    "consequence_reason": [],
    "fallback_reason": null
  },
  "p_utility_negative": null,
  "rau": {
    "value": 0.31,
    "band": "proceedable",
    "source_type": "derived"
  },
  "confidence_percent": 83,
  "confidence_band": "high",
  "decision": "proceed",
  "requires_confirmation": true,
  "why": [
    "Risk budget 50% used: expected_loss 0.10 divided by C3 threshold 0.20.",
    "Value After Risk is Positive after the uncertainty penalty.",
    "Confidence is 83% after repo evidence gathering.",
    "Auth code is C3, so confirmation is required."
  ]
}
```

`scope_basis` is `symbol` when symbol evidence was used, `file_fallback` when parsing or mapping was unavailable, and `unknown_fallback` when PEBRA had to score conservatively with degraded evidence. Ordinary risk cards must use this block so cosmetic/test-only edits in C4, payment, or god-node files are not described with the same risk basis as behavioral or contract changes to consequential symbols.

Default human-readable rendering:

```text
PEBRA Decision: Proceed, but confirm first

Risk Level: Moderate
Code Sensitivity: High
Confidence: High
Value After Risk: Positive

Why:
- This touches auth-related code, so mistakes have higher impact.
- Affected Area is low: the planned edit is small, reversible, and has limited local usage.
- Rollback is simple because the expected change is limited to the target function and test.
- A targeted auth test exists.

Required Guardrails:
- Make the smallest sufficient patch.
- Run the targeted auth test before finalizing.
- Commit on a new branch if running autonomously.
```

Worked example values must be computed from stated formulas, not manually invented. A future docs check should parse examples and fail if derived values drift.

### 9.3 High-Risk Trigger Flags

High-risk trigger flags are machine-readable evidence, not decisions. They explain why PEBRA entered `sensitive_context`, `elevated_review`, or `controlled_high_risk`, or why it returned `ask_human` / `reject` for a high-risk condition.

Required trigger shape:

```json
{
  "trigger_id": "hrt_001",
  "risk_class": "payment_side_effect",
  "trigger_source": "symbol_diff",
  "severity": "critical",
  "affected_scope": "src/payments/charge.py::charge_customer",
  "evidence": [
    "change_kind=SIDE_EFFECT",
    "payment_api_changed=true",
    "criticality_stage=C4"
  ],
  "decision_effect": "requires_controlled_high_risk_mode",
  "control_blueprint_id": "payment_change",
  "required_controls": [
    "sandbox_payment_tests",
    "idempotency_evidence",
    "reconciliation_baseline"
  ],
  "suppressible": false,
  "suppress_reason": null,
  "provenance": {
    "source_type": "derived",
    "provider": "pebra"
  }
}
```

Allowed `trigger_source` values: `symbol_diff`, `criticality`, `blast_radius`, `policy`, `learned_fact`, `evidence_gap`, `gate`.

Allowed `severity` values: `elevated`, `high`, `critical`.

Trigger rules:

- A trigger may explain `test_first`, `ask_human`, `reject`, or `controlled_high_risk`, but it does not create a sixth decision.
- A trigger can map to a `control_blueprint_id`; the controls become binding only when copied into the guidance packet's binding fields or sanction requirements.
- A possible trigger suppressed by verified `COSMETIC` or safe `TEST_ONLY` classification must still be auditable with `suppressible=true` and a `suppress_reason`.
- Trigger content is dynamic and evidence-derived. It must come from symbol diffs, criticality, blast radius, policy, learned facts, evidence gaps, and gate results, not from hardcoded model-aware syntax or an LLM-authored prompt.

### 9.4 Model Guidance Packet

PEBRA should return a model-facing guidance packet alongside the human card and canonical scores. This packet folds PEBRA's deterministic risk decision back into the editing model, but it must not become a second reasoning system.

The packet is a deterministic rendering of PEBRA outputs:

| Field | Source |
|---|---|
| `safe_scope` | approved candidate action envelope |
| `risky_scope` | project-derived risky changes, each with an action enum |
| `required_checks_before_commit` | decision, test discovery, criticality, confidence gates |
| `required_controls` | selected high-risk control blueprint, if any |
| `high_risk_triggers` | trigger flags from §9.3 |
| `risk_facts` | risk report metrics and top drivers |
| `value_facts` | benefit breakdown, maintainability/debt/durability drivers, and top value tradeoffs |
| `why` | explanation generator and top risk drivers |
| `suggested_inspection` | `inspect_first` / `test_first` evidence actions |
| `safer_alternative` | selected lower-risk action or decision-engine recommendation |
| `better_value_alternative` | selected higher-Value-After-Risk alternative when multiple candidates are available |

Required shape:

```json
{
  "guidance_packet_id": "gp_123",
  "decision": "test_first",
  "risk_mode": "elevated_review",
  "binding": {
    "safe_scope": {
      "files": ["src/components/data/SpreadsheetView.tsx"],
      "edit_policy": "targeted_patch_only"
    },
    "risky_scope": [
      {"change": "dependency upgrades", "action": "requires_reassessment"},
      {"change": "schema changes", "action": "requires_reassessment"},
      {"change": "public API changes", "action": "requires_reassessment"}
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
    "value_facts": {
      "value_after_risk": "borderline",
      "top_value_driver": "targeted patch preserves short-term task value while avoiding broad refactor debt",
      "maintainability_delta": "derived_neutral",
      "technical_debt_interest": "low",
      "durability": "uncalibrated"
    },
    "why": [
      "Touched code is an architecture anchor / god node with high affected area.",
      "If the fix requires dependency, schema, or public API changes, the current assessment must be recomputed."
    ],
    "suggested_inspection": ["inspect local call sites", "inspect grid/formula tests"],
    "safer_alternative": "make a targeted patch instead of refactoring the grid state model",
    "better_value_alternative": "targeted patch plus focused regression test has better Value After Risk than a broad refactor"
  },
  "provenance": {
    "safe_scope": "candidate_action.expected_files",
    "risky_scope": "policy_gates + detected_risk_events + architecture map + learned facts",
    "required_checks_before_commit": "test_discovery + recommended_decision",
    "required_controls": "high_risk_triggers + control blueprint selector",
    "high_risk_triggers": "symbol_diff + criticality + gates + learned facts",
    "risk_facts": "risk_report + evidence discovery",
    "value_facts": "benefit_breakdown + value_model + candidate comparison",
    "why": "explanation_generator"
  }
}
```

Binding fields are the pre-edit autonomy envelope. `pebra_verify` must enforce them after the edit by checking actual diff scope, contract-surface changes, dependency/schema/migration changes, required controls, and required checks. Advisory fields guide the editing model but do not create new hard gates.

Trigger flags in the guidance packet are advisory evidence for the model. Required controls are binding. This distinction keeps PEBRA understandable without letting the model decide that a high-risk trigger is optional.

`risky_scope` is assessment-invalidating by default, not banned by default. The risk score was computed under assumptions about scope; touching a `requires_reassessment` item makes the assessment stale and forces a new assessment. Only `action: forbidden` is a hard reject.

Allowed `risky_scope.action` values:

| Action | Meaning | Verify behavior |
|---|---|---|
| `requires_reassessment` | The current risk score no longer applies if this change is touched | Route to `inspect_first` / reassessment |
| `avoid_unless_required` | Allowed only with evidence that the task cannot be solved safely inside `safe_scope` | Route to `ask_human` if touched without necessity evidence |
| `forbidden` | Project policy prohibits this under the current action | Route to `reject` |

Enforcement map:

| Binding field | Verification input | Failing result |
|---|---|---|
| `safe_scope` | actual diff files/symbols vs approved envelope | `inspect_first` / reassessment for reviewable drift; `ask_human` or `reject` for broad or unrelated drift |
| `risky_scope` | lockfile/schema/migration/API/dependency/security-sensitive diff checks | Apply the entry's action enum |
| `required_checks_before_commit` | `completed_checks[]` plus check output provenance | `test_first` when checks are missing; `ask_human` when checks failed but the action is still needed |

`pebra_verify` should accept `completed_checks[]` so required checks are evidence-backed instead of assumed.

Guidance is derived, not authored. PEBRA must not ask an LLM to invent these constraints. An output adapter may render them as prompt text for a model, but the content must be reconstructable from the canonical response.

Because guidance changes the model's behavior, outcomes produced under a guidance packet should record:

```text
guidance_packet_id
binding_constraints
advisory_hints
calibration_scope = guided_edit
```

Precedence rule: if `guidance_packet_id` is present, `calibration_scope` must be `guided_edit` unless a later implementation explicitly models guidance as a feature and reports separate calibration curves.

Guided outcomes must be analyzed separately from unguided outcomes unless calibration explicitly models the guidance condition.

---

## 10. Worked Example

This example has two stages. Stage 1 recommends information gathering. Stage 2 is the reassessment after that evidence is gathered.

### 10.1 Stage 1: Initial Assessment

```text
Task: Fix failing login validation

| Action                         | Action Type | RAU   | Confidence | Decision      |
|--------------------------------|-------------|-------|------------|---------------|
| Inspect auth tests/call sites  | information | --    | --         | inspect_first |
| Patch validate_login only      | edit        | 0.24  | Low        | inspect_first |
| Refactor auth module           | edit        | -0.70 | Low        | reject        |
| Upgrade auth dependency        | edit        | -0.54 | Low        | ask_human     |
```

Why:

- The targeted patch is promising but starts low confidence because call sites and tests are not inspected.
- The information action is cheap and likely to reduce uncertainty.
- Broad refactor and dependency upgrade have poor conservative utility.

### 10.2 Stage 2: Reassessment After Evidence

Evidence gathered:

- Targeted auth test path was found.
- Local call-site search found limited dependent usage.
- No schema, dependency, migration, or external-state write was detected.

```text
| Action                    | Benefit | P(success) | Expected Loss | Review Cost | Utility SD | RAU   | Confidence | Decision |
|---------------------------|---------|------------|---------------|-------------|------------|-------|------------|----------|
| Patch validate_login only | 0.82    | 0.74       | 0.10          | 0.12        | 0.06       | 0.31  | High       | proceed  |
| Refactor auth module      | 0.88    | 0.41       | 0.58          | omitted     | omitted    | -0.70 | Low        | reject   |
| Upgrade auth dependency   | 0.63    | 0.48       | 0.49          | omitted     | omitted    | -0.54 | Low        | ask_human |
```

Computed patch values:

```text
expected_utility =
  0.74 * 0.82 - 0.10 - 0.12
  = 0.3868
  ≈ 0.39

risk_adjusted_utility =
  0.3868 - 1.28 * 0.06
  = 0.3100
  ≈ 0.31

edit_confidence =
  geometric_mean(0.74, 0.78, 0.80, 0.92, 0.86, 0.92)
  = 0.8338
  ≈ 0.83
```

Because confidence upgraded from low to high, the response uses `recommended_decision: "proceed"` and `requires_confirmation: true`.

### 10.3 Canonical Response Example

```json
{
  "schema_version": "0.1",
  "task": "Fix failing login validation",
  "repo_id": "repo_local_example",
  "repo_root": "/abs/path/to/example-repo",
  "assessed_commit": "abc123",
  "risk_snapshot_id": "R0",
  "prediction_error_model_id": "E0",
  "recommended_decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
  "risk_mode": "sensitive_context",
  "high_risk_triggers": [],
  "decision_reason": "Repo-local evidence reduced uncertainty; targeted patch has positive RAU.",
  "risk_report": {
    "risk_type": "risk_budget_indicator",
    "headline_risk_percent": 50,
    "expected_loss": {
      "value": 0.10,
      "source_type": "derived",
      "provider": "pebra"
    },
    "risk_budget_used_percent": 50,
    "budget_threshold_used": {
      "key": "c3_max_expected_loss_without_human",
      "value": 0.20,
      "reason": "Auth code is C3 and the C3 threshold is tighter than the global threshold."
    },
    "symbol_scope_evidence": {
      "scope_basis": "symbol",
      "changed_symbols": ["src/auth.py::validate_login"],
      "max_change_kind": "BEHAVIORAL",
      "visibility": "internal",
      "symbol_fan_in_percentile": 0.42,
      "consequential_symbol_changed": false,
      "consequence_reason": [],
      "fallback_reason": null
    },
    "p_utility_negative": null,
    "rau": {
      "value": 0.31,
      "band": "proceedable",
      "source_type": "derived"
    },
    "confidence_percent": 83,
    "confidence_band": "high",
    "decision": "proceed",
    "requires_confirmation": true,
    "top_risk_drivers": [
      {
        "event": "test_regression",
        "expected_loss": 0.04,
        "share_of_loss_percent": 40,
        "why": "Prior regression risk remains until the targeted auth test is run."
      },
      {
        "event": "security_sensitive_change",
        "expected_loss": 0.04,
        "share_of_loss_percent": 40,
        "why": "The change touches auth behavior, mapped to C3 criticality by project policy."
      },
      {
        "event": "public_api_break",
        "expected_loss": 0.02,
        "share_of_loss_percent": 20,
        "why": "Call-site search found limited usage, so API break contribution is lower."
      }
    ],
    "protective_factors": [
      "Small targeted patch",
      "Targeted auth test exists",
      "No dependency, schema, migration, or external-state change detected",
      "Rollback is straightforward"
    ],
    "why": [
      "Risk budget 50% used: expected_loss 0.10 divided by C3 threshold 0.20.",
      "Value After Risk is Positive after the uncertainty penalty.",
      "Confidence is 83% after repo evidence gathering.",
      "Auth code is C3, so this is sensitive context and confirmation is required."
    ]
  },
  "actions": [
    {
      "id": "info_1",
      "label": "Inspect auth tests and local call sites",
      "action_type": "information",
      "action_status": "completed",
      "information_value": {
        "value": 0.16,
        "level": "level_2",
        "source_type": "derived",
        "provider": "pebra",
        "confidence": 0.80,
        "formula": "expected_uncertainty_reduction - info_cost - info_delay_cost",
        "evidence": ["Read-only local inspection found tests and limited call sites."]
      }
    },
    {
      "id": "a1",
      "label": "Patch validate_login only",
      "action_type": "edit",
      "action_status": "pending",
      "risk_mode": "sensitive_context",
      "high_risk_triggers": [],
      "edit_control": {
        "initial_confidence_band": "low",
        "confidence_band": "high",
        "requires_confirmation": true,
        "confidence_transition": {
          "from": "low",
          "to": "high",
          "upgrade_allowed": true,
          "reason": "Repo-local evidence reduced uncertainty about call sites and testability.",
          "evidence_delta": {
            "missing_before": [
              "Call sites for validate_login were not inspected.",
              "Targeted test coverage was unknown."
            ],
            "gathered_now": [
              "Local call-site search found limited usage.",
              "Targeted auth test exists."
            ],
            "uncertainty_reduced": [
              "blast_radius",
              "testability",
              "scope_control"
            ],
            "remaining_risks": [
              "Benefit and success probability are still partly estimated until the patch is tested."
            ]
          }
        },
        "edit_policy": "smallest_sufficient_edit; no broad refactor"
      },
      "scores": {
        "benefit": {
          "value": 0.82,
          "level": "level_1",
          "source_type": "elicited",
          "provider": "user",
          "confidence": 0.70,
          "evidence": ["Directly addresses the failing login-validation task."],
          "method": "MCDA value function with normalized criterion weights"
        },
        "benefit_breakdown": {
          "immediate_benefit": {
            "value": 0.82,
            "source_type": "elicited",
            "provider": "user",
            "confidence": 0.70,
            "evidence": ["Fixing login validation directly resolves the task."]
          },
          "maintainability_delta": {
            "value": 0.00,
            "source_type": "derived",
            "provider": "pebra",
            "confidence": 0.70,
            "evidence": ["Proposed targeted patch does not change complexity, coupling, public surface, or testability."]
          },
          "technical_debt_interest": {
            "value": 0.01,
            "source_type": "derived",
            "provider": "pebra",
            "confidence": 0.60,
            "evidence": ["Small localized patch adds no duplicated path and touches an active C3 auth scope."]
          },
          "recurrence_risk": {
            "value": 0.08,
            "source_type": "prior_uncalibrated",
            "provider": "pebra",
            "confidence": 0.45,
            "evidence": ["No repo-local recurrence history is available yet."]
          },
          "expected_rework_cost": {
            "value": 0.02,
            "source_type": "derived",
            "provider": "pebra",
            "formula": "recurrence_risk * rework_cost_per_recurrence",
            "evidence": ["Recurrence cost is tied to C3 auth consequence, not ordinary review cost."]
          }
        },
        "p_success": {
          "value": 0.74,
          "level": "level_1",
          "source_type": "estimated",
          "provider": "model",
          "calibration_status": "estimated_uncalibrated",
          "confidence": 0.62,
          "evidence": ["Localized action with targeted test plan and no dependency change."]
        },
        "criticality": {
          "criticality_stage": "C3",
          "criticality_value": 0.80,
          "level": "level_1",
          "source_type": "configured",
          "provider": ".pebra.yml",
          "confidence": 0.95,
          "evidence": [".pebra.yml maps src/auth/** to C3."]
        },
        "expected_loss": {
          "value": 0.10,
          "level": "level_2",
          "source_type": "derived",
          "provider": "pebra",
          "confidence": 0.62,
          "formula": "sum(p_event_j * disutility_j)",
          "components": [
            {
              "event": "test_regression",
              "p_event": 0.10,
              "disutility": 0.40,
              "expected_loss": 0.04,
              "probability_source_type": "prior_uncalibrated",
              "disutility_source_type": "elicited"
            },
            {
              "event": "public_api_break",
              "p_event": 0.03,
              "disutility": 0.80,
              "expected_loss": 0.02,
              "probability_source_type": "measured",
              "probability_provider": "sem",
              "disutility_source_type": "elicited"
            },
            {
              "event": "security_sensitive_change",
              "p_event": 0.04,
              "disutility": 0.90,
              "expected_loss": 0.04,
              "probability_source_type": "configured",
              "probability_provider": ".pebra.yml",
              "disutility_source_type": "elicited",
              "criticality_stage": "C3",
              "criticality_value": 0.80,
              "disutility_method": "max(elicited_disutility, criticality_floor)",
              "floor_applied": false
            }
          ]
        },
        "review_cost": {
          "value": 0.12,
          "level": "level_1",
          "source_type": "estimated",
          "provider": "pebra",
          "confidence": 0.76,
          "evidence": ["Expected diff is localized to one implementation file and one test file."]
        },
        "utility_sd": {
          "value": 0.06,
          "level": "level_2",
          "source_type": "derived",
          "provider": "pebra",
          "confidence": 0.65,
          "method": "first_order_error_propagation",
          "variance_breakdown": {
            "p_success": 0.0016,
            "benefit": 0.0004,
            "event_losses": 0.0009,
            "review_cost": 0.0004,
            "scenario_variance": 0.0003,
            "total_variance": 0.0036
          }
        },
        "expected_utility": {
          "value": 0.39,
          "level": "level_2",
          "source_type": "derived",
          "provider": "pebra",
          "confidence": 0.60,
          "formula": "p_success * benefit - expected_loss - review_cost"
        },
        "risk_adjusted_utility": {
          "value": 0.31,
          "level": "level_2",
          "source_type": "derived",
          "provider": "pebra",
          "confidence": 0.60,
          "formula": "expected_utility - z_alpha * utility_sd",
          "parameters": {
            "confidence_level": 0.90,
            "z_alpha": 1.28
          }
        },
        "edit_confidence": {
          "value": 0.83,
          "level": "level_2",
          "source_type": "derived",
          "provider": "pebra",
          "confidence": 0.60,
          "formula": "weighted_geometric_mean",
          "factors": {
            "p_success": 0.74,
            "evidence_quality": 0.78,
            "testability": 0.80,
            "reversibility": 0.92,
            "source_reliability": 0.86,
            "scope_control": 0.92
          }
        }
      },
      "decision": "proceed"
    }
  ],
  "thresholds_used": {
    "max_expected_loss_without_human": 0.45,
    "c3_max_expected_loss_without_human": 0.20,
    "effective_expected_loss_threshold": 0.20,
    "max_p_negative_utility": 0.10,
    "max_utility_sd_without_human": 0.20,
    "decision_instability_threshold": 0.10,
    "high_edit_confidence": 0.75,
    "low_edit_confidence": 0.50,
    "rau_bands": {
      "reject_below": 0.00,
      "borderline_below": 0.15,
      "strong_at": 0.40
    }
  }
}
```

### 10.4 Monte Carlo Example

This is a separate hypothetical borderline action, not the `validate_login` patch above:

```text
Expected utility: 0.39
First-order RAU 90% lower bound: 0.31
Monte Carlo RAU 90% lower bound: 0.22
P(utility < 0): 0.14
P(action is best): 0.82
5th percentile utility: -0.05
```

Here the 5th percentile is negative, which is consistent with `P(utility < 0) = 0.14`.

---

## 11. Architecture and Modules

```text
CLI / MCP / Dashboard surfaces
   |
   v
app/ use-case controllers
   +-- assess_controller
   +-- verify_controller
   +-- record_outcome_controller
   +-- accept_risk_controller
   +-- learning_controller
   |
   +-- call ports for evidence, store, outcomes, sanctions, learning
   +-- call core engines for deterministic scoring and decisions
   |
   v
core/ pure engines
   +-- Request/schema validator
   +-- Candidate action parser
   +-- Decision query validator
   +-- Assessment builder
   +-- Score normalizer
   +-- Weight resolver
   +-- Confidence gate
   +-- Benefit model
   +-- Score math
   +-- Decision engine
   +-- Change classifier
   +-- High-risk control selector
   +-- Explanation generator

adapters/ implement ports:
   git diff/status, structural metrics, import/call/dependency graphs,
   architecture map, security static analysis, test discovery, repo config,
   SQLite store, sanction store, outcome logger, calibration/learning store.
```

`cli/`, `mcp_server/`, and `dashboard/` are entrypoints, not controllers. `app/` owns orchestration. `core/` owns deterministic business logic and must not import adapters, dashboard code, SQLite, subprocess, or CLI parsing.

### 11.1 Assessment Object

The in-flight assessment object passed between modules should contain:

```json
{
  "schema_version": "0.1",
  "request": {},
  "candidate_actions": [],
  "evidence": {},
  "scores": {},
  "thresholds": {},
  "risk_snapshot_id": "R0",
  "prediction_error_model_id": "E0",
  "gates": {},
  "decision": null,
  "provenance": {}
}
```

Benefit calibration state lives inside the active snapshot's `benefit_model` section; there is no separate `benefit_snapshot_id` in v1.

---

## 12. Config Reference

```yaml
risk_tolerance: 0.55

criticality:
  "src/auth/**": C3
  "src/payments/**": C4
  "src/migrations/**": C4
  "src/ui/**": C2
  "tests/**": C1
  "docs/**": C0

architecture:
  enabled: true
  source_of_record: pebra_repo_scan
  optional_enrichment_sources:
    - ARCHITECTURE.md
    - graphify-out/ANCHORS.md
    - graphify-out/graph.json
    - codegraph
  stale_graph_policy: sync_then_inspect_first_or_fail_closed
  baseline_rebuild_policy: rebuild_if_stale
  god_node_percentile: 0.95
  bridge_node_percentile: 0.95

thresholds:
  max_expected_loss_without_human: 0.45
  c3_max_expected_loss_without_human: 0.20
  c3_requires_confirmation: true
  c4_always_ask_human: true
  c4_requires_confirmation: true
  max_p_negative_utility: 0.10
  max_utility_sd_without_human: 0.20
  decision_instability_threshold: 0.10
  ask_on_negative_rau: false           # AD-2: if true, RAU < 0 escalates to ask_human instead of reject
  min_monte_carlo_sample_count: 10000
  high_edit_confidence: 0.75
  low_edit_confidence: 0.50
  max_retrieval_only_confidence: 0.90
  require_evidence_delta_for_low_confidence_upgrade: true
  require_user_confirmation_for_low_confidence_upgrade: true
  # medium_auto_proceed_requires: v1.5 reserved. The flags below are not
  # computed in v1; medium-band auto-proceed is governed by the re-score +
  # gate sequence instead (see Architecture AD-6).
  # medium_auto_proceed_requires:
  #   - targeted_checks_pass
  #   - residual_blast_radius_low
  #   - no_policy_violation

rau_bands:
  reject_below: 0.00
  borderline_below: 0.15
  strong_at: 0.40

edit_confidence_weights:
  p_success: 1/6
  evidence_quality: 1/6
  testability: 1/6
  reversibility: 1/6
  source_reliability: 1/6
  scope_control: 1/6

monte_carlo:
  disutility_triangular_by_stage:
    C0: [0.05, 0.10, 0.20]
    C1: [0.20, 0.30, 0.45]
    C2: [0.35, 0.50, 0.70]
    C3: [0.65, 0.80, 0.92]
    C4: [0.85, 1.00, 1.00]

learning:
  min_observed_predictions_for_auto_promotion: 100
  min_observed_risk_predictions_for_auto_promotion: 100
  min_observed_benefit_predictions_for_auto_promotion: 100
  benefit_status_when_below_min_n: pending_min_n
  require_holdout_brier_improvement: true
  require_false_proceed_not_worse: true
  max_false_block_rate: 0.25
  max_auto_promotion_delta: 0.10
  auto_promote_measurement_facts: true
  auto_promote_policy_facts: false
  decouple_risk_and_benefit_promotion: true
  exclude_benefit_guidance_influenced_rows_by_default: true
  var_benefit_narrowing_requires_observed_benefit_outcome: true
  projected_benefit_variance_floor: 0.04
  projected_benefit_zero_gate_credit_without_evidence: true
  fact_decay:
    enabled: true
    default_decay_strength: 20
    min_effective_weight: 0.10
    use_scope_churn_not_wall_clock: true
  promotion:
    require_counterfactual_replay: true
    min_delta_brier_for_promotion: 0.00
    min_delta_log_loss_for_promotion: 0.00
    freeze_on_reconciliation_drift: true
    max_snapshot_drift_without_review: 0.10
  reapplication:
    top_k: 1                         # v1 fallback; v1.5 may raise to 3
    probability_pooling: hard_replace # hard_replace | weighted_log_pool
    max_logit_shift: 2.0              # secondary safety clamp, not the primary control
    semantic_probability_floor: 0.01
    semantic_probability_ceiling: 0.99

preferred_graph_engine: codegraph

evidence:
  file_size:
    high_loc: 1000
    critical_loc: 3000
    high_percentile: 0.90
    critical_percentile: 0.95
  fan_in:
    # Absolute caller counts are display-only context. Gate-driving fan-in
    # must use repo-relative percentiles so small repos and monorepos behave
    # consistently.
    high_percentile: 0.90
    critical_percentile: 0.95
  imports:
    high_module_import_fan_in_percentile: 0.90
    critical_symbol_import_fan_in_percentile: 0.95
    dynamic_import_band: moderate
    circular_import_band: critical
    third_party_import_change_band: high
  complexity:
    moderate_cyclomatic: 11
    high_cyclomatic: 21
    critical_cyclomatic: 50
```

### 12.1 Outcome Learning and Calibration Contract

PEBRA's learning loop is official product behavior, not only an implementation note.

Each assessment must pin the scoring state it used:

```json
{
  "risk_snapshot_id": "R17",
  "prediction_error_model_id": "E17"
}
```

These IDs make decisions replayable. PEBRA must not mutate the active snapshot during an in-flight assessment. A background learning job may create a candidate snapshot for a future assessment, but promotion only advances the active snapshot pointer.

PEBRA learns from observable probability errors, not directly from RAU. The primary calibration targets are:

```text
p_success
p_event.<event_class>
```

For every predicted probability, PEBRA should store:

```text
prediction_id
assessment_id
action_id
risk_snapshot_id
prediction_error_model_id
guidance_packet_id
target
calibration_bucket
scope_basis
symbol_id
change_kind
visibility
fan_in_percentile_bucket
affected_scope
predicted_probability
actual_outcome
outcome_label_status
calibration_scope
```

`target` uses canonical names such as `p_success`, `p_event.dependency_break`, `p_event.public_api_break`, or `p_event.response_shape_mismatch`. It never stores human labels.

The symbol/scope fields preserve the feature bucket that produced the prediction. Without them, learning can blur a cosmetic edit in a god node with a behavioral edit to an exported symbol in the same file. When symbol evidence is unavailable, set `scope_basis=file_fallback` or `unknown_fallback` and record the fallback reason in the assessment payload.

After an outcome is known:

```text
residual = actual_outcome - predicted_probability
brier_error = residual^2
log_loss = -log(clamp(probability assigned to the actual outcome, LOG_LOSS_CLIP_EPS, 1 - LOG_LOSS_CLIP_EPS))
```

Use `LOG_LOSS_CLIP_EPS = 1e-15` for every log-loss calculation. This keeps confident-wrong predictions finite and makes golden regression output deterministic.

Brier score is the primary bounded calibration error. Log loss is a surprise signal for drift and review, not a single-example reweighting rule.

RAU, risk-budget, confidence, and decision errors are diagnostics. They should explain failures and route learning, but they must not be optimized directly because there is no clean observed "true RAU." RAU improves when `p_success` and `p_event_j` become better calibrated.

### 12.2 Selective-Label Guard

Calibration must label what was actually observed:

| Field | Allowed Values | Meaning |
|---|---|---|
| `outcome_label_status` | `observed`, `censored`, `counterfactual` | Whether the outcome was actually seen |
| `calibration_scope` | `proceeded_edits_only`, `guided_edit`, `shadow`, `canary`, `benchmark` | Which population the calibration claim covers |

PEBRA must not claim full calibration across all actions when outcomes only exist for actions it allowed. Training views for automatic recalibration should include only observed prediction rows, for example:

```text
WHERE outcome_label_status = "observed"
AND calibration_scope = "proceeded_edits_only"
AND guidance_packet_id IS NULL
```

Rejected or blocked actions may still store predictions, but they are censored unless a later CI, benchmark, shadow run, or human-reviewed experiment produces an observable outcome.

Guided edits are observed, but not identical to unguided edits: the model received a binding/advisory packet that may reduce mistakes. Store `guidance_packet_id` and use `calibration_scope = "guided_edit"` when the packet materially shaped the edit. Automatic recalibration may combine guided and unguided outcomes only when the model includes guidance as a feature or reports separate calibration curves.

Canary and benchmark rows are valid outcome evidence, but they are not part of the default production calibration view. They should feed separate validation reports unless the fitted model explicitly stratifies by `calibration_scope`.

Guidance compliance rows are valid learning evidence, but they should not be mixed into the default unguided probability-calibration view. They answer a different question: whether PEBRA's guidance helped the model stay inside the approved envelope.

### 12.3 Two-Tier Learning Rules

PEBRA separates measurement learning from value/policy learning.

Tier 1 may be autonomously recalibrated after gates pass:

- `p_success` priors and calibration.
- `p_event_j` priors and calibration.
- source reliability.
- edge-confidence weights.
- evidence-quality variance.
- repo risk memory backed by observed outcomes.

Tier 2 may be suggested autonomously but requires human ratification:

- criticality stage changes such as `src/payments/**: C3 -> C4`.
- risk thresholds.
- business-damage or disutility policy.
- C4 applicability rules.
- risk tolerance.
- widening `safe_scope` or making it less specific.
- downgrading `risky_scope.action`, for example `requires_reassessment -> avoid_unless_required`.
- removing or weakening required checks.

Promotion gates for Tier 1:

```text
auto_promote only if:
  holdout Brier/log-loss improves or does not regress
  false-proceed rate does not rise
  C4 / high-criticality decisions do not weaken
  change magnitude <= max_auto_promotion_delta
  selective-label checks pass
```

If drift, surprise, or shadow/canary divergence worsens, PEBRA freezes auto-promotion and falls back to the previous snapshot.

### 12.3.1 Guidance Learning Signals

The model guidance packet is both an output of learning and a new source of learning evidence.

Read path:

```text
learned_risk_facts
-> active risk_snapshot
-> apply_snapshot()
-> adjusted risk inputs
-> model_guidance_packet safe_scope / risky_scope / checks / risk_facts / why
```

The same learned fact may affect both channels:

```text
"dependency upgrades fail often in this repo"
  -> raises p_event.dependency_break
  -> adds or strengthens a risky_scope entry for dependency changes
```

Write path:

```text
model_guidance_packet
-> agent edit
-> pebra_verify
-> guidance compliance labels
-> guided-learning report / learned_risk_facts candidates
```

`pebra_verify` should record guidance-compliance labels:

| Field | Meaning |
|---|---|
| `guidance_packet_id` | Which packet the model received |
| `safe_scope_status` | `respected`, `exceeded`, or `unrelated_drift` |
| `risky_scope_triggered[]` | Which risky-scope entries were touched |
| `risky_scope_actions_triggered[]` | Which action enums fired |
| `completed_checks[]` | Required checks completed with status/provenance |
| `missing_checks[]` | Required checks not completed |
| `failed_checks[]` | Required checks that failed |
| `necessity_evidence_present` | Whether an `avoid_unless_required` change had evidence that it was necessary |
| `verify_decision` | The resulting `proceed`, `inspect_first`, `test_first`, `ask_human`, or `reject` decision |

Examples of learned facts from guidance compliance:

```text
models exceed safe_scope on broad_refactor requests in this repo
dependency upgrades touched under auth scope often trigger reassessment
tests/test_auth.py catches most auth-guided regressions
public API risky_scope entries usually need ask_human rather than inspect_first
```

Autonomous measurement learning may adjust probabilities, source reliability, evidence quality, scope-drift priors, and required-check effectiveness. Policy learning must be human-ratified before it changes future guidance. PEBRA must not silently widen `safe_scope`, downgrade `risky_scope.action`, remove checks, or relax forbidden entries.

Guidance policy learning is counterfactual-sensitive. If PEBRA always tells the model to avoid a change, it does not observe whether that change would have been safe. Therefore, guidance-policy changes should rely on AD-18 counterfactual replay, shadow/canary evidence, benchmark rows, or human-reviewed experiments, not raw guided-outcome frequency alone.

### 12.4 Applying Learned Risk to the Next Assessment

PEBRA must reapply learned risk through a deterministic read path at the start of the next assessment:

```text
previous outcomes
-> prediction_errors
-> learned_risk_facts
-> promoted risk_snapshot
-> next assessment loads active snapshot
-> apply_snapshot()
-> adjusted inputs
-> normal scoring pipeline
```

The required pure function is:

```text
apply_snapshot(raw_inputs, active_snapshot, promoted_facts) -> adjusted_inputs
```

It runs after request validation and evidence collection, but before score normalization, expected loss, RAU, edit confidence, variance propagation, Monte Carlo, and decision gates.

`apply_snapshot` may adjust measurement inputs:

| Learned Fact Type | Next Assessment Effect |
|---|---|
| calibrated `p_success` | replaces or adjusts raw `p_success` |
| calibrated `p_event.*` | replaces or adjusts event probability priors |
| edge-confidence reliability | adjusts `source_reliability` or `evidence_quality` |
| repeated scope drift | adds gate pressure toward `inspect_first` or `ask_human` |
| repo risk memory | adjusts priors by path, symbol, dependency, or action class |
| ratified criticality bump | adjusts criticality only after human ratification |
| ratified threshold/policy change | adjusts policy only after human ratification |

Scope matching uses deterministic precedence:

```text
symbol
> file/path glob
> dependency
> action_type
> global
> cold-start default
```

If two active learned facts have the same specificity, the best-calibrated / highest-evidence fact wins. If still tied, the lowest stable `fact_id` wins. This avoids order-dependent behavior in the append-only fact store.

Guardrails:

```text
Learning may adjust inputs.
Learning may not rewrite formulas.
Learning may not silently lower criticality.
Learning may not auto-apply value/policy facts without ratification.
Learning may not mutate an assessment already in progress.
```

The scoring pipeline remains unchanged after reapplication. Learned facts improve the inputs; they do not replace expected-loss, RAU, confidence, or gate formulas.

AD-16 is the v1 default and the k=1 fallback for later composition. With `learning.reapplication.probability_pooling: hard_replace`, the selected fact directly replaces or adjusts the prior according to its method. With `weighted_log_pool`, the selected fact is pooled with the base prior using the AD-20 method.

### 12.5 Decay-By-Weight, Not Deletion

PEBRA should not delete learned facts merely because they become stale. The audit ledger must remain append-only. Instead, `apply_snapshot` uses an effective recall weight:

```text
effective_weight = base_weight * exp(-scope_change_count / decay_strength)
```

Where:

| Term | Meaning |
|---|---|
| `base_weight` | The learned fact's original strength after promotion |
| `scope_change_count` | Commits, edits, or verified changes touching the fact's scope since it was learned |
| `decay_strength` | How much scoped churn the fact survives before weakening |
| `effective_weight` | The weight used by `apply_snapshot` on the next assessment |

Decay is scope-driven, not wall-clock-driven. A fact about stable code should not fade simply because time passed. A fact about a rewritten module should fade because its evidence may no longer apply.

High-evidence facts may decay more slowly:

```text
decay_strength increases with:
  confirming_outcome_count
  positive counterfactual replay delta
  low rolling Brier/log-loss
```

If a fact's effective weight falls below `learning.fact_decay.min_effective_weight`, PEBRA should stop applying it automatically and append a `risk_fact_decayed` event. The original fact remains queryable for audit. Retiring or deleting value/policy facts remains a human-ratified governance action.

### 12.6 Counterfactual Promotion and Snapshot Reconciliation

Promotion must test whether a learned fact actually improves future decisions.

Before promotion, PEBRA should replay historical assessments twice:

```text
error_without_fact = replay historical assessments with candidate fact disabled
error_with_fact    = replay historical assessments with candidate fact enabled

delta_brier    = error_without_fact.brier - error_with_fact.brier
delta_log_loss = error_without_fact.log_loss - error_with_fact.log_loss
```

A measurement fact may be promoted only if:

```text
delta_brier >= learning.promotion.min_delta_brier_for_promotion
delta_log_loss >= learning.promotion.min_delta_log_loss_for_promotion
false_proceed_rate does not increase
C4 / high-criticality decisions do not weaken
selective-label checks pass
```

This gate prevents promotion of facts that are large but not useful.

Snapshot reconciliation protects against incremental drift. Periodically, PEBRA should rebuild a candidate snapshot from the raw append-only ledger:

```text
raw assessments + outcomes + prediction_errors
-> recompute learned_risk_facts
-> rebuild candidate risk_snapshot
-> compare candidate snapshot to active snapshot
```

If snapshot drift exceeds `learning.promotion.max_snapshot_drift_without_review`, PEBRA freezes auto-promotion and requires review. Reconciliation may roll back to a prior active snapshot by changing the active pointer; it must not mutate historical rows.

Before a candidate learned fact enters a snapshot, PEBRA should run a contradiction gate. If the fact conflicts with a ratified policy or criticality rule, route it to human review instead of silently applying or deleting it.

### 12.7 Learning Loop Evaluation Harness

PEBRA must be able to prove that learning improves decisions. The evaluation harness should replay historical assessments in chronological order:

```text
for each assessment in time order:
  score with genesis/no-learning snapshot
  score with the active learned snapshot available at that time
  record Brier, log-loss, risk-budget, decision, and guardrail outcomes
```

Required comparison:

```text
baseline = genesis snapshot with apply_snapshot disabled
variant  = learned snapshots with apply_snapshot enabled
```

Report:

| Metric | Why It Matters |
|---|---|
| rolling Brier / log-loss | whether probability calibration improves |
| calibration slope/intercept | whether PEBRA remains over- or under-confident |
| false-proceed rate | whether learning weakens safety |
| C4 / high-criticality weakening count | whether safety-critical behavior regresses |
| contradiction rate | whether learned facts conflict with ratified policy |
| staleness distribution | whether facts are decaying or staying useful |
| rework / repeated-failure reduction | whether PEBRA stops repeating bad actions |

Learning is only valuable if the replay curve improves against the no-learning baseline. If it does not, PEBRA should keep the facts for audit but avoid promoting them into the active snapshot.

The executable harness lives outside the production core:

```text
benchmarks/flow/
  corpus/
    requests/*.json
    outcomes/*.json
    expected_decisions/*.json
  replay.py
  scorecard.py
```

It has two modes:

| Mode | Purpose | Labels Needed |
|---|---|---|
| Deterministic flow regression | Freeze realistic requests and assert the same decisions/scores are produced from the same snapshot | No |
| Learning-lift evaluation | Replay labeled outcomes chronologically and compare active learning against genesis/no-learning | Yes |

Deterministic flow regression should run on every commit. It catches accidental changes to math, gates, scope handling, or guidance rendering:

```text
same request corpus
+ same risk_snapshot
+ same prediction_error_model
= same scores, decisions, guidance, and guardrail outputs
```

Learning-lift evaluation should run on labeled corpora:

```text
run A: genesis/no-learning snapshot, apply_snapshot disabled
run B: active learning, snapshots advance only when promotion gates pass
compare scorecards
```

Minimum scorecard:

| Metric | Gate |
|---|---|
| Brier/log-loss delta | active learning improves or does not regress |
| false-proceed rate | must not increase |
| C0-C2 false-block / over-escalation rate | report; gate after enough labels |
| high-criticality decision weakening | must not occur without ratified policy |
| decision drift | every material flip must cite the learned fact or snapshot that caused it |
| guidance compliance | report safe-scope drift, risky-scope triggers, and check completion deltas |

The first corpus should be small and curated. Synthetic labels are acceptable for the initial harness if they are explicit and reviewed. Later corpora may include SWE-bench-style tasks, private PR logs, and PEBRA's own outcomes, but proceeded-only outcomes must keep their `observed/censored/counterfactual` labels to avoid selective-label bias.

### 12.8 Top-k Learned Fact Composition

Top-k composition lets PEBRA use several relevant learned facts for the same target instead of only the single most-specific fact.

It is ratified for v1.5 / Phase 6. AD-16 remains the v1 default and the `top_k = 1` fallback.

Composition is partitioned by target:

```text
p_success facts combine only with p_success facts
p_event.dependency_break facts combine only with p_event.dependency_break facts
source_reliability facts combine only with source_reliability facts
evidence_quality facts combine only with evidence_quality facts
```

For probability targets (`p_success`, `p_event.<event_class>`), use reliability-weighted logarithmic pooling:

```text
candidates = {base_prior p0} + top_k matching learned probabilities

raw_weight_i =
  specificity_factor_i
  * calibration_quality_i
  * evidence_count_factor_i
  * decay_weight_i

normalized_weight_i = raw_weight_i / sum(raw_weight)

logit(p*) = sum_i normalized_weight_i * logit(p_i)
p* = sigmoid(logit(p*))
```

This is a weighted expert-opinion pool in odds space. Because the weights sum to 1, correlated warnings cannot stack without bound. A secondary safety clamp may bound `abs(logit(p*) - logit(p0))`, but this clamp is not the primary control.

PEBRA must clamp probability inputs to a semantic certainty range before pooling:

```text
p_i = clamp(p_i, learning.reapplication.semantic_probability_floor,
                 learning.reapplication.semantic_probability_ceiling)
```

The default range `[0.01, 0.99]` is a policy statement that PEBRA should not claim certainty, not a numerical machine-epsilon trick.

For [0,1] reliability targets such as `source_reliability` and `evidence_quality`, use the same normalized weights but pool in linear space:

```text
x* = sum_i normalized_weight_i * x_i
x* = clamp(x*, 0, 1)
```

Top-k candidate selection:

```text
rank by:
  specificity
  calibration_quality
  evidence_count
  effective_weight
  stable fact_id

take top_k per target
```

Provenance must record each applied fact:

```json
{
  "target": "p_event.dependency_break",
  "pooling_method": "weighted_log_pool",
  "base_prior": 0.25,
  "pooled_result": 0.61,
  "applied_facts": [
    {
      "fact_id": "fact_42",
      "scope": "src/api/billing/**",
      "specificity_tier": "path_glob",
      "raw_weight": 0.72,
      "weight_share": 0.60,
      "effective_weight": 0.81
    }
  ],
  "safety_clamp_engaged": false
}
```

Human-facing explanation should summarize the weighted signals, for example:

```text
Risk was raised by two learned signals:
billing-path history (60% weight) and major-dependency upgrade history (40% weight).
```

v1.5+ upgrade: when enough outcomes exist, PEBRA may replace hand-set weights with logistic-regression stacking for probability targets using `scikit-learn`. This must remain provenance-tagged and gated by enough calibration data.

### 12.9 Typed Scope/Action DAG

The typed scope/action DAG is the ratified v2 / Phase 7 matching model. It complements AD-20:

```text
AD-21 finds candidate learned facts.
AD-20 combines them safely.
```

The DAG represents scopes as typed nodes:

```text
repo
path_glob
symbol
dependency
action_type
event_target
```

Example:

```text
repo:pebra
  -> path_glob:src/api/**
      -> path_glob:src/api/billing/**
          -> symbol:calculateInvoice
  -> dependency:react
  -> action_type:dependency_upgrade
```

The DAG is serialized inside immutable snapshot JSON:

```json
{
  "metrics_json": {
    "scope_dag": {
      "nodes": [],
      "edges": []
    }
  }
}
```

`learned_risk_facts.scope_node_id` may point to a DAG node when the DAG is enabled. This is a forward-only additive field; older facts without a `scope_node_id` still match by the AD-16 string/glob rules.

Traversal is deterministic:

```text
1. Find all scope nodes matching the candidate action.
2. Reduce to maximal / most-dominant matching nodes per axis.
3. Collect candidate facts from those nodes and their allowed ancestors.
4. Pass candidates to AD-20 top-k composition.
```

Orthogonal axes compose. For example, a single edit may match:

```text
path_glob:src/api/billing/**
dependency:react
action_type:dependency_upgrade
event_target:p_event.dependency_break
```

Rejected for the core scorer:

- REINFORCE or GRPO memory policies.
- learned softmax node weights.
- embedding-based matching.
- LLM-authored traversal rules.
- non-deterministic graph traversal.

The DAG is data, not a model. It improves matching and provenance without changing the expected-loss, RAU, confidence, or gate formulas.

### 12.10 Model Guidance Packet Ratification

AD-23 is ratified here: model guidance is the pre-edit autonomy envelope.

PEBRA may render deterministic guidance for the editing model, but the guidance is derived from PEBRA's own approved action, gates, and evidence. It is not authored by the model and it is not a second decision system. JSON is the canonical audit representation; adapters may render the same facts as an MCP payload, prompt text, PR card, or CLI summary.

Binding fields:

| Binding field | Meaning | Post-edit enforcement |
|---|---|---|
| `safe_scope` | Files, symbols, dependencies, or action envelope the model may touch normally | `pebra_verify` compares actual diff scope to this envelope |
| `risky_scope` | Changes that require reassessment, human review, or rejection depending on their action enum | `pebra_verify` applies each entry's action: `requires_reassessment`, `avoid_unless_required`, or `forbidden` |
| `required_checks_before_commit` | Checks that must be completed before autonomous commit/PR | `pebra_verify` requires matching entries in `completed_checks[]` with status/provenance |

Advisory fields such as `risk_facts`, `why`, `suggested_inspection`, and `safer_alternative` may guide the model, but they do not create hard gates.

`safe_scope` and `risky_scope` must be project-aware and provenance-traced. Cold-start defaults may include common risky changes such as dependency upgrades, schema/migration edits, public API changes, and broad refactors. Project-specific entries should be derived from `.pebra.yml`, criticality stages, the architecture map, evidence discovery, and promoted learned facts. The model must not invent risky-scope entries.

Guided edits are a separate calibration population. `outcomes` and `prediction_errors` must carry nullable `guidance_packet_id`; if it is present, the row's `calibration_scope` must be `guided_edit` unless a later model explicitly includes guidance as a calibration feature. The default production calibration view must require `guidance_packet_id IS NULL`.

`pebra_verify` should persist guidance compliance labels: `safe_scope_status`, `risky_scope_triggered[]`, `risky_scope_actions_triggered[]`, `completed_checks[]`, `missing_checks[]`, `failed_checks[]`, `necessity_evidence_present`, and `verify_decision`. These labels power §12.3.1 guidance learning.

The packet must be stored and hash-chained so a later reviewer can reconstruct exactly what the model was allowed to do.

Outcome logging is a v1 product requirement, but Phase 0 may ship schema-only until CLI `pebra record-outcome` and MCP `pebra_record_outcome` support land in the MCP + outcomes phase. Calibration reports, dashboard learning panels, and automatic learning require stored outcomes.

### 12.11 Multi-Repository Runtime and Repo-Scoped State

PEBRA should support many local repos on one developer machine without mixing risk history. The authoritative state for a repo is local to that repo:

```text
<repo>/
  .pebra.yml                  # committed team policy
  .pebra/
    .gitignore                # auto-written; keeps local state out of git
    pebra.db                  # repo-local decision + learning source of truth
    config                    # gitignored machine-local repo config
    dashboard.json            # last dashboard port / pid / URL metadata
    architecture_cache/       # rebuildable derived artifacts
    scorecards/               # local project scorecards, not benchmark corpora
```

Repo resolution:

```text
current working directory
-> walk upward for .pebra/
-> otherwise walk upward for .git/
-> initialize .pebra/ at the resolved repo root
```

Every assessment, guidance packet, outcome, prediction error, learned fact, risk snapshot, architecture projection, and dashboard page is scoped to exactly one repo-local store. PEBRA must not place learned risk facts or active snapshots in a machine-global database by default.

A small machine registry is allowed for discovery and dashboard convenience:

```text
Windows: %APPDATA%\pebra\registry.json
Linux/macOS: $XDG_STATE_HOME/pebra/registry.json or ~/.local/state/pebra/registry.json
```

The registry may store known repo roots, display names, git remotes, last dashboard port, last seen time, and repo-local DB paths. It is not a learning store. Deleting the registry must not delete repo risk history.

`repo_id` should be derived from normalized git remote URL plus the resolved repo root. If no remote exists, PEBRA should create a stable local ID in `.pebra/config`. Responses should include `repo_id` and `repo_root` so the user and agent know which project the decision came from.

Worktree rule: if `.git` is a file rather than a directory, PEBRA should treat the checkout as a git worktree. The safe default is to create a `.pebra/` inside that worktree so parallel agents/branches do not share assessments, learning, or active snapshots accidentally. If the user explicitly opts into sharing the parent repo store, PEBRA should warn and label the shared state in CLI, MCP, and dashboard output.

Configuration precedence:

```text
CLI flags / env vars
-> .pebra.yml committed team policy
-> <repo>/.pebra/config machine-local repo config
-> machine registry / global config
-> defaults
```

`.pebra.yml` owns project policy, risk thresholds, and criticality. `.pebra/config` owns local paths, dashboard port preference, and machine-only settings. Policy changes should not be hidden in machine-local config.

Cross-repo learning is out of scope for v1. Future global/org priors may exist only as weak cold-start priors and must never override repo-specific learned facts or human-ratified project policy.

Hash-chain append rule: every append-only decision table write must be serialized. The store adapter must use a write transaction such as:

```sql
BEGIN IMMEDIATE;
-- read current tail hash
-- compute new integrity_hash
-- insert row
COMMIT;
```

The adapter should set a `busy_timeout`. WAL permits concurrent readers, but it does not by itself protect the tail-hash read/compute/insert sequence from two writer processes racing against the same previous hash. CLI, MCP, dashboard verify flows, and background learning jobs must all write through the same store adapter. An optional `.pebra/write.lock` may add an advisory process lock, but the SQLite transaction is the required correctness boundary.

### 12.12 Controlled High-Risk Mode

Some high-risk work is business-mandated: database migrations, payment system upgrades, security fixes, or director-approved production changes. PEBRA must not pretend these actions are safe, but it should support a controlled path for doing them.

Controlled high-risk is a companion mode, not a sixth decision:

```text
before approval:
  decision = ask_human
  risk_mode = controlled_high_risk

after authorized acceptance + mandatory controls:
  decision = proceed
  risk_mode = controlled_high_risk
  requires_confirmation = true
```

`risk_mode=controlled_high_risk` requires at least one `high_risk_triggers[]` entry. PEBRA must show the user and model which trigger fired and which control blueprint was selected; it must not collapse a mandatory migration, payment upgrade, or director-approved risky action into a bare `reject`.

The score remains honest:

```text
expected_loss unchanged
risk_adjusted_utility unchanged
edit_confidence unchanged
risk_budget_used unchanged
```

Risk acceptance adds controls; it never lowers risk. Required controls should be selected from a deterministic blueprint keyed by risk class, change kind, criticality, affected area, evidence quality, and project policy. Examples:

| Risk Class | Required Control Examples |
|---|---|
| database migration | backup/restore point, staging dry-run, expand-contract plan, rollback or roll-forward plan, data validation baseline, post-migration verification |
| payment change | sandbox payment tests, idempotency evidence, webhook tests, reconciliation baseline, duplicate-charge guard, ledger checks |
| public API / contract | contract tests, consumer-shape checks, versioning/compatibility plan, dependent test run |
| UI/user journey | headed or CI Playwright E2E for affected journey, visual/wiring checks, rollback plan |
| broad/god-node behavioral edit | impact preview, targeted tests, smoke tests, code-owner or human approval |

Risk acceptance is created only through an explicit use case, not by the scoring engine:

```text
CLI: pebra accept-risk --assessment-id ... --action-id ... --rationale ...
MCP: pebra_accept_risk
app: app/accept_risk_controller.py
port: SanctionPort
store: sanction_events
```

The controller verifies that the caller is permitted to ratify risk, records `ratified_by` and non-empty `rationale`, binds the sanction to the approved risk profile, and writes a hash-chained `sanction_events` row.

Each selected blueprint records its trigger linkage:

```text
high_risk_triggers[]
control_blueprint_id
pre_edit_authorization_controls[]
pre_commit_required_controls[]
suppressed_triggers[]
```

`pre_edit_authorization_controls` must be satisfied before a risk-threshold `ask_human` / `reject` can become `proceed` with `risk_mode=controlled_high_risk`. `pre_commit_required_controls` are binding guidance and are verified later by `pebra_verify`; missing or failed controls invalidate the sanction before commit or successful outcome logging.

Suppressed triggers are retained for audit when the classifier verifies `COSMETIC` or safe `TEST_ONLY` and therefore avoids nuisance high-risk mode.

Risk acceptance is bound to the risk profile that was approved:

```text
repo_id
assessment_id
action_id
risk_snapshot_id
assessed_commit
guidance_packet_id
safe_scope_hash
risky_scope_hash
required_controls_hash
control_blueprint_id
risk_report_hash
```

Risk acceptance must be stored as an append-only `sanction_events` record, not as a mutable flag on the assessment:

```text
sanction_id
assessment_id
action_id
repo_id
risk_snapshot_id
assessed_commit
guidance_packet_id
sanction_type
sanction_scope
status
ratified_by
rationale
safe_scope_hash
risky_scope_hash
required_controls_hash
control_blueprint_id
risk_report_hash
expires_at
invalidated_reason
```

If evidence becomes stale, scope drifts, the actual symbol diff is more severe than the proposed patch, or required controls change, the risk acceptance is invalidated and the action returns to `inspect_first` or `ask_human`.

Default sanction scope is one action. Standing/scoped sanctions are out of the default path and must be narrow: risk class, path glob, short expiry, explicit ratifier, and stronger audit. A sanction may override risk-threshold gates such as C4 escalation, expected-loss threshold, RAU default reject, or Monte Carlo negative-utility gates. It must not silently override a hard project policy violation; policy exceptions require a distinct, higher-scrutiny sanction type.

### 12.13 Symbol-Level Risk Resolution

AD-27 is ratified here: symbol-level risk resolution is PEBRA's canonical risk model. File/path criticality tells PEBRA where to inspect harder and how severe consequence-bearing failures could be, but the edited symbol and semantic change kind decide normal likelihood features, Affected Area, guidance, learning buckets, and whether high-risk mode is warranted.

Symbol-level criticality resolution:

```text
edited_consequence_symbol_stage =
  max(stage(symbol) for edited symbols where change_kind in
      {BEHAVIORAL, CONTRACT, SIDE_EFFECT, DIRECTIVE, UNKNOWN}
      and symbol is consequential)

path_or_capability_stage = stage matched from .pebra.yml / capability detection

effective_criticality_stage =
  if edited_consequence_symbol_stage exists:
    max(edited_consequence_symbol_stage, path_or_capability_stage)
  elif verified_change_kind in {COSMETIC, safe TEST_ONLY}:
    sensitive_context_only(path_or_capability_stage)
  else:
    conservative_file_fallback(path_or_capability_stage)
```

In practice, file/path and capability criticality still raise the consequence level for behavioral/contract/side-effect edits to sensitive domains such as payments, auth, migrations, and external state. They do not by themselves make a cosmetic or safe test-only edit a consequential high-risk edit. Parser or fan-in failure uses `UNKNOWN` and falls back conservatively.

PEBRA should classify proposed and actual edits with `SymbolDiffProvider` and `core/change_classifier.py`. Required invariants:

- C4 path alone is not enough to trigger controlled high-risk mode.
- payment path alone is not enough.
- god-node / architecture-anchor status alone is not enough.
- `COSMETIC` and safe `TEST_ONLY` edits do not trigger controlled high-risk mode solely from file membership.
- `BEHAVIORAL`, `CONTRACT`, `SIDE_EFFECT`, `DIRECTIVE`, or `UNKNOWN` changes to consequential symbols may trigger elevated review or controlled high-risk mode.
- parser/fan-in failure is conservative: use `UNKNOWN`, not low risk.
- regular `risk_report`, `p_event`, review cost, and Affected Area must use symbol/scope evidence when available, not only high-risk triggers.
- finalized `high_risk_triggers[]` are assembled by core from symbol evidence, scores, gates, policy, learned facts, and evidence gaps; adapters emit raw evidence only.

Consequential symbols are identified by exported/public status, repo-relative fan-in percentile, transitive reach to consequence-bearing symbols, side-effect profile, or capability signals such as payment, DB write, migration, deletion, external state, idempotency, retry, or transaction-boundary behavior.

`pebra_verify` must rerun the full classifier on the actual diff and compare it with the pre-edit packet. If the actual change is more severe, the original assessment and any risk acceptance are invalidated:

```text
pre-edit: COSMETIC
actual:   BEHAVIORAL on C4 payment symbol
result:   scope_drift_detected = true, ask_human / reject
```

This closes the body-change blind spot that contract-surface scanning cannot catch.

### 12.14 Comparative Benefit Model

AD-28 is ratified here: PEBRA optimizes auditable net value under risk constraints, not risk minimization. Risk constrains action; benefit justifies action. Maintainability is a first-class measured economic outcome of living software, not an optional fuzzy bonus. The existing expected-utility and RAU formulas remain canonical. AD-28 changes how the scalar `benefit` is resolved and explained.

Required invariants:

- `benefit_breakdown` is upstream of RAU; it is not a second decision engine.
- The final scalar `benefit` is still the only benefit input consumed by `expected_benefit = p_success * benefit`.
- Benefit components must carry provenance, confidence, and variance.
- Unsupported or strategy-only long-term value claims widen `Var(benefit)` and therefore lower RAU through the uncertainty penalty; code-derived maintainability metrics are deterministic projections with variance tied to evidence coverage.
- Strategic/business value and value-policy weights are Tier-2 value judgments; PEBRA may suggest them from outcomes but must not auto-promote them without ratification.
- Recurrence and durability probabilities are Tier-1 measurement targets when outcomes exist, because reverts, re-edits, follow-up regressions, and reopened issues are observable.
- Recurrence cost must be scaled to the affected scope's expected-loss/disutility scale, not to ordinary review cost.
- Maintainability deltas are `derived` from a concrete proposed patch AST/diff, `projected` from a strategy without a patch, and `measured` after `pebra_verify` sees the actual diff.
- Benefit deltas use before/after metric comparison, directional normalization, and future-exposure weighting as defined in §5.1.1.
- `projected` strategy-only deltas may explain alternatives but receive no gate-driving credit unless configured or ratified.
- Positive maintainability benefit follows confirm-before-credit: it receives gate-driving weight only when backed by concrete patch metrics, configured policy, or verified outcomes. Negative maintainability deltas, new debt interest, and recurrence/rework costs count immediately when detected.
- Net-benefit style ranking is the primary alternative-comparison lens. ICER-style pairwise ratios are diagnostic only.
- PEBRA ranks quantified alternatives only when the request supplies multiple candidate actions. A single-action assessment may render a qualitative `better_value_alternative`, but it must not invent a fully quantified alternative set.

Outcome labels that support future benefit calibration include:

```text
task_accepted
issue_reopened
reverted
followup_regression
reedit_required
review_comments_count
time_to_merge
tests_added
tests_caught_failure
maintainability_delta_measured
debt_interest_observed
```

Benefit learning follows AD-15's two-tier rule:

```text
measurement: p_recurrence, p_success, p_event.*, source reliability -> autonomous calibration
value/policy: strategic value, discount rate, maintainability weights, debt tolerance -> human-ratified suggestion
```

### 12.15 Decoupled Risk and Benefit Learning

AD-29 is ratified here: benefit learning uses the same outcome stream as risk learning, but it must not share a promotion gate with risk learning.

One outcome can create multiple prediction-error rows:

```text
risk targets:
  p_success
  p_event.<event_class>
  p_recurrence_as_harm

benefit binary targets:
  immediate_benefit_realized
  task_accepted
  recurrence_avoided

benefit continuous targets:
  maintainability_delta
  review_cost
  expected_rework_cost
  technical_debt_interest
  net_benefit_score
```

Required invariants:

- `prediction_errors` must carry `target_type`: `risk_binary`, `benefit_binary`, or `benefit_continuous`.
- Binary targets populate `predicted_probability`, `actual_outcome`, `residual`, `brier_error`, and `log_loss`; continuous benefit targets populate `predicted_value`, `actual_value`, `residual`, and `squared_error`.
- `brier_error` and `log_loss` are NULL for `benefit_continuous`; `squared_error` is NULL for `risk_binary` and `benefit_binary`.
- Risk binary targets use Brier/log-loss and feed the risk calibration view.
- Benefit binary targets use Brier/log-loss but feed a separate benefit-binary calibration view.
- Benefit continuous targets use residual/MSE/MAE and feed a separate benefit-continuous calibration view.
- PEBRA must not average risk Brier/log-loss and benefit continuous errors into one promotion score.
- Risk promotion gates only on risk calibration and never waits for benefit promotion.
- Benefit promotion gates only on benefit calibration and may remain `pending_min_n` without blocking risk promotion.
- Once benefit calibration reaches `min_observed_benefit_predictions`, benefit gates become active and stay visible; a low-volume repo must surface `benefit_status=pending_min_n` rather than silently skipping benefit forever.
- Benefit promotion never recomputes or invalidates a promoted risk snapshot.
- A snapshot may store both `risk_model` and `benefit_model` sections, but each section has its own status, activation time, min-N, and promotion metrics.

Variance guard:

```text
Var(benefit) may narrow only from observed benefit outcomes:
  measured maintainability_delta
  observed review_cost
  observed rework / recurrence
  verified technical_debt_interest evidence
```

The following must not narrow `Var(benefit)`:

```text
cross-fact confidence propagation
LLM confidence
unratified strategic value
generic learned optimism
```

Because narrowing `Var(benefit)` raises RAU, variance narrowing is economically equivalent to increasing the action's attractiveness and must be governed as carefully as the benefit magnitude.

Benefit-guidance selective-label guard:

```text
benefit_guidance_influenced = true
```

must be set when model guidance selected, encouraged, or constrained the action based on benefit claims such as maintainability, debt reduction, durability, or better-value alternative. Default benefit calibration views exclude or stratify these rows. This prevents PEBRA from learning an optimism loop where it recommends the edits it already believes are beneficial and then calibrates only on those proceeded edits.

Off-policy honesty rule:

PEBRA may rank candidate actions prospectively by expected net benefit. It must not claim observed calibration of unchosen alternatives unless an off-policy method with logged propensity or controlled exploration is explicitly implemented. Off-policy benefit evaluation is a benchmark/v2 concern, not a default v1 production claim.

---

## 13. MVP Scope and Build

### 13.1 MCP and CLI

v1 should include:

- MCP tool `pebra_compare`.
- Optional CLI command `pebra assess`.
- CLI command `pebra verify`.
- CLI command `pebra accept-risk`.
- MCP tool `pebra_verify`.
- MCP tool `pebra_accept_risk`.
- Optional convenience wrapper `pebra_assess` for a single action.
- JSON input/output using `schema_version: "0.1"`.
- Human-readable table generated from canonical response.

Roadmap:

- `pebra_explain`.

`pebra_verify` closes the autonomy loop after an edit and before commit, PR, or successful outcome logging. It takes a stored `assessment_id`, a verification `scope`, and optional `completed_checks[]`; checks evidence freshness; compares the actual diff against `safe_scope`, `risky_scope`, and the other binding fields of `model_guidance_packet`; detects contract-surface changes; and returns the same five-decision vocabulary. It does not create a sixth decision; failures route through `inspect_first`, `test_first`, `ask_human`, or `reject`.

### 13.2 v1 Should Include

- Canonical request and response schemas.
- Decision enum and state machine.
- Tier-1 evidence discovery:
  - LOC and complexity via `radon`.
  - CodeGraph-backed symbol fan-in/fan-out and edge provenance.
  - PEBRA-built architecture anchors and domain map from repo scan.
  - Symbol-level diff classification for proposed and actual edits.
  - Python SAST via `bandit`.
  - Blast radius through `sem` when available.
  - Git diff/status and targeted test discovery.
- MCDA-derived benefit and disutility scales.
- Weight-source ladder with provenance.
- Elicited-weight consistency checks.
- Small-n guard for objective weighting.
- Expected-loss event model.
- Risk-adjusted utility scoring.
- Edit-confidence scoring.
- Rank-gap fallback when Monte Carlo is unavailable.
- Repo-level `.pebra.yml`.
- Repo-scoped `.pebra/pebra.db` state, `.pebra/.gitignore` initialization, machine-local `.pebra/config`, and a small machine registry for repo discovery/port reuse.
- Worktree detection and safe default isolation for parallel agent branches.
- Serialized hash-chain appends using `BEGIN IMMEDIATE` so concurrent CLI/MCP/dashboard-adjacent writes cannot fork the audit log.
- Symbol-level risk resolution so cosmetic/test-only edits to C4/god-node/payment files do not trigger high-risk mode solely from file membership.
- Post-edit full symbol reclassification so actual behavioral/contract/side-effect drift cannot evade verify.
- Outcome logging schema.

### 13.3 v1.5 Should Add

- Multi-language import/dependency adapters.
- Call graph adapters beyond the first supported language.
- Maintainability Index and coverage mapping where tools are available.
- Objective weights such as CRITIC/Entropy when candidate count and criterion variance are sufficient.
- Configured triangular ranges for judgment-input uncertainty.
- Method sensitivity report for weight/rank stability.
- Monte Carlo decision gates when validated distributions or calibrated outcome data exist.
- Automatic measurement learning from `prediction_errors`, with fact decay, counterfactual promotion, and snapshot reconciliation.
- Decoupled risk/benefit learning tracks with separate calibration views, min-N gates, and promotion gates.
- Top-k learned-fact composition with reliability-weighted logarithmic pooling.
- Learning-loop evaluation report comparing active snapshots against a genesis/no-learning baseline.
- Architecture-map comparison/enrichment adapters for Graphify / legacy codeindex artifacts in benchmark or research mode.

### 13.4 v1 Should Not Include

- Owning a full in-process code graph engine; CodeGraph is the external graph engine and PEBRA owns interpretation.
- Full multi-language evidence discovery.
- Generic MCDA method catalogue or MCDA studio UI.
- Runtime EVPI, EVPPI, CEAC, or PSA.
- Automatic edits.
- Claims of universal correctness.
- Risk labels used as direct model inputs without measured evidence.
- RL-trained memory policies, embeddings, or LLM-written gate parameters in the core scorer.
- Building a new full code graph platform inside PEBRA.
- Broad vendor-specific integrations before the core loop works.

### 13.5 Success Criteria

PEBRA v1 is useful if:

- It makes agents choose narrower edits when broad edits have poor expected value.
- It discovers structural risk from measured repo signals.
- It detects architecture anchors / god nodes from local architecture maps and explains their impact separately from criticality.
- It recommends tests/inspection when uncertainty could change the decision.
- It avoids overconfident action when confidence is low.
- It produces rationales humans can audit.
- It integrates through MCP without changing the agent runtime.
- It works across many local repos on the same machine without sharing learned facts or corrupting repo-specific audit chains.
- It can serve a local dashboard without assuming the default port is free.
- It never emits a bare high-risk `ask_human` or `reject`: high-risk routes include `risk_mode`, `high_risk_triggers[]`, mapped control blueprints, and required controls or suppression reasons.
- It avoids nuisance high-risk triggers and nuisance ordinary risk cards by classifying the edited symbol/change kind, while still escalating one-line behavioral changes to consequential payment, migration, or public API symbols.
- It records symbol/scope provenance in risk reports and prediction-error buckets so learning distinguishes cosmetic/test-only edits in sensitive files from consequential symbol changes.

---

## 14. Evaluation

### 14.1 Sources

| Source | Use |
|---|---|
| SWE-bench Verified | Pilot and historical baseline only |
| SWE-bench Pro | Better frontier benchmark target when available |
| Private repo task logs | Best real-world calibration source |
| Generated candidate patches | Needed because benchmarks usually provide tasks, not action choices |

### 14.2 Protocol

For each task:

1. Generate 3 to 6 candidate actions with the same base agent.
2. Run PEBRA before final test outcomes are known.
3. Let PEBRA rank actions or request information.
4. Execute the selected action.
5. Record whether the issue was resolved.
6. Record regressions, review burden, files touched, and test results.

### 14.3 Metrics

| Metric | Question |
|---|---|
| Selection accuracy | Did PEBRA choose the best candidate action? |
| Regression avoidance | Did PEBRA avoid high expected-loss failed edits? |
| Structural signal validity | Do structural signals improve event prediction? |
| Brier score | Is `p_success` calibrated? |
| Calibration slope/intercept | Is the model systematically overconfident? |
| Human escalation precision | Were escalations useful? |
| Information-gathering precision | Did `inspect_first` or `test_first` reduce failure? |
| Review cost reduction | Did PEBRA avoid noisy broad diffs? |
| Monte Carlo decision value | When enabled, did `P(utility < 0)` or `P(action is best)` improve borderline decisions? |

### 14.4 Product-Quality Validation

Unit, property, and golden tests prove the plumbing. This layer proves the product: are PEBRA's risk numbers correct, and does PEBRA make agents code better?

AD-19's executable form is the `benchmarks/flow` harness:

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

The harness runs the real pipeline through real adapters deterministically: snapshots are pinned, benchmark manifests pin data versions, and regression mode must not depend on wall-clock time or unseeded randomness.

Validation tracks:

| Track | Oracle / Baseline | Question | License / Scope |
|---|---|---|---|
| Deterministic flow regression | frozen golden corpus | Do identical requests, snapshots, and models produce byte-identical scores, decisions, guidance, and guardrail outputs? | runs on every commit |
| Structural agreement | codeindex / radon / bandit; GitNexus as optional external comparator | Does `affected_area` / centrality agree with established impact tools? | GitNexus is external-only, never shipped |
| Calibration oracle | ApacheJIT / JIT-Defects4J labels plus logistic JIT-DP baseline | Are `p_success` and `p_event.*` calibrated and discriminative? | dataset version pinned |
| Learning lift | genesis/no-learning snapshot | Does active learning beat cold-start after outcomes accumulate? | chronological replay |
| Agent efficacy | SWE-bench Verified/Live with-vs-without PEBRA | Do guided agents introduce fewer regressions without losing resolved rate? | long-running benchmark tier |
| Comparator | TDAD-style graph-impact regression-reduction work when available | How does PEBRA compare to the closest graph-impact agent-safety baseline? | comparator-only |

GitNexus may be used as an external benchmark comparator, like one model benchmarking against another. It is never imported, vendored, shipped, or required by PEBRA.

Reproducibility rule: every scorecard records dataset name, dataset version/split/commit, comparator tool version/commit, PEBRA git commit, `risk_snapshot_id`, `prediction_error_model_id`, and `calibration_scope`. Benchmark numbers are only comparable when both code and data are pinned.

#### 14.4.1 Scorecard Math

For prediction `p_i` in `(0,1)` and observed outcome `y_i` in `{0,1}`:

```text
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

Brier/log-loss are lower-is-better promotion gates, so positive lift is `baseline - learned`. AUC-PR, AUC-ROC, and Spearman agreement are higher-is-better, so positive lift is `learned - baseline`. ECE is reported and used for reliability diagrams, but not alone as a hard gate because it is bin-sensitive. AUC-PR is the primary discrimination metric for defect/oracle corpora because harmful outcomes are usually imbalanced; AUC-ROC is secondary.

Outcome labels use a consistent convention: `y=1` means harmful / bug-inducing and `y=0` means safe. Dataset adapters must map their oracle into this convention: SZZ/JIT corpora map bug-inducing commits to `y=1`; SWE-bench-style agent runs map failed or regression-introducing patches to `y=1`; live PEBRA outcomes map terminal failed/regression statuses to `y=1`.

`core/learning_eval.py` should contain only pure stdlib metric primitives: Brier, log loss, ECE bin aggregation, false-proceed/false-block arithmetic, decision-rate summaries, and lift arithmetic. `benchmarks/flow/scorecard.py` owns pandas/scipy aggregation, bootstrap confidence intervals, AUC-PR/AUC-ROC, reliability diagrams, plots, and report rendering.

#### 14.4.2 Benchmark Gates

A release or snapshot promotion fails if any condition holds:

| Gate | Failure Condition |
|---|---|
| Calibration | learned Brier/log-loss regresses against genesis or prior snapshot beyond the configured confidence interval |
| Safety | false-proceed rate increases |
| Over-blocking | C0-C2 false-block rate exceeds `max_false_block_rate` after enough labels |
| High-criticality safety | any C3/C4 decision weakens from held to proceed without ratified policy |
| Explainability | material decision flip or risk-band crossing lacks cited `fact_id` / `snapshot_id` |
| Determinism | regression mode is not byte-identical across repeated runs |
| Evidence sufficiency | corpus size is below `N_min` |

This is the product-quality test layer. CLI smoke tests prove commands run; the flow harness proves whether PEBRA scores, decides, verifies, and learns better than its baselines.

### 14.5 Risk Observatory Dashboard

PEBRA should include a self-hosted, operator-facing web UI. The dashboard is not a benchmark report and not a SaaS app. It is how operators inspect what PEBRA is doing in the current repo and whether its real recorded outcomes make it more or less trustworthy.

Surface split:

```text
cli/          one-shot human card
mcp_server/   agent integration
dashboard/    self-hosted Risk Observatory
core/         unchanged; no UI imports
```

`pebra dashboard` reads SQLite/API/scorecard artifacts and renders existing facts. In v1 it is read-only: it must not recompute risk formulas, promote snapshots, edit project policy, or mutate assessment state.

Multi-repo behavior:

- Default view is the current repo resolved by walking up for `.pebra/` or `.git/`.
- The machine registry may provide a repo switcher, but every dashboard route must be scoped to one repo, for example `/repos/<repo_id>/...`.
- Each dashboard page reads that repo's `.pebra/pebra.db`; the registry is not a global learning store.
- `pebra dashboard --repo <path>` registers or refreshes that repo and opens it.
- `pebra dashboard --all` may open the registry landing page, but it still reads per-repo stores.

Port behavior:

- Base port is `9473`.
- Reuse a live port from `<repo>/.pebra/dashboard.json` when it belongs to the current repo.
- `--port` or `PEBRA_PORT` pins the port and fails fast if unavailable.
- `--port 0` asks the OS for an ephemeral port.
- Without an explicit port, try `9473` and then bounded auto-increment.
- `--instance N` maps to `9473 + N*100` for explicit side-by-side daemons.
- Host allowlist and printed/opened URLs must use the actually bound port.

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

The dashboard must not display benchmark harness results. ApacheJIT, JIT-Defects4J, SWE-bench, TDAD, and GitNexus comparator reports remain developer/research artifacts under `benchmarks/flow`, CI artifacts, release notes, or docs. The dashboard displays only production/project state derived from PEBRA's assessment store, outcome store, learning snapshots, guidance compliance, architecture map, and audit chain.

Security posture:

- bind to `127.0.0.1` by default.
- require bearer authentication for API access.
- reject unapproved `Host` headers to prevent DNS rebinding.
- self-host all static assets.
- avoid remote CDNs.
- use a CSP nonce.
- require explicit opt-in plus allowed-host configuration for non-loopback binding.
- display which `repo_id` / `repo_root` the page is reading, especially when the machine registry exposes multiple repos.

Playwright validates the visual layer, not the math. The headed visual E2E suite should open the local dashboard against fixture SQLite/API data, assert cards/tables/charts match fixture values, verify learning charts and reliability diagrams are nonblank, scrub the replay timeline, render guidance compliance, render audit/hash-chain status, fail on console errors, and save screenshots/traces for review. Metric correctness remains covered by core tests and `benchmarks/flow` scorecards.

---

## 15. Open Design Questions

The following are intentionally left open for product decisions; they do not block Phase 0:

1. Should PEBRA ever generate candidate actions, or should it permanently score only actions supplied by the agent/user?
2. Which first agent adapter should receive polished UX first: Codex, Claude Code, Cursor, or generic MCP?
3. How much of the Risk Observatory should ship in the first public release versus after the CLI/MCP loop proves useful?
4. Should future org/global priors exist only as benchmark/research artifacts, or as weak cold-start priors configurable by teams?

---

## 16. Related Work and Positioning

PEBRA should claim integration novelty, not primitive novelty.

Expected utility, calibration, abstention, blast-radius analysis, criticality tagging, risk-adaptive escalation, and post-generation code assurance already exist in separate fields. PEBRA's contribution is the integration point: a pre-edit decision controller for coding agents that combines repo-grounded blast radius, criticality/stakes, calibrated confidence, expected loss, and RAU into a five-way action decision before the agent edits.

### 16.1 Positioning Claim

PEBRA is not another blast-radius graph or post-hoc code scanner. It is a pre-edit decision controller that integrates repo-grounded blast radius, criticality/stakes, and calibrated confidence into an expected-loss/RAU score, then emits one of five decisions:

```text
proceed | inspect_first | test_first | ask_human | reject
```

The defensible distinctions are:

| Distinction | PEBRA Position |
|---|---|
| Timing | Pre-edit, before the agent changes code |
| Action space | Five-way decision, not only proceed/reject |
| Evidence model | Repo-grounded blast radius plus criticality/stakes |
| Decision math | Expected loss, RAU, confidence gates, and Monte Carlo gates when fitted/configured distributions exist |
| Auditability | Provenance on scores, distributions, and evidence actions |

### 16.2 GitHub and Platform Neighbors

Blast-radius and code graph tools are useful evidence providers for PEBRA, but they should not be described as equivalent systems.

| Neighbor | What It Covers | Distinction From PEBRA |
|---|---|---|
| `code-impact-mcp` | MCP-style code impact / blast-radius gate such as pass, warn, or block | Single-axis impact gate; no benefit, criticality, RAU, confidence state machine, or five-way action enum |
| `Ctxo` | Repo context, dependency information, and safe-edit style guardrails | Primarily context and edit-safety support; not an expected-loss decision controller |
| CodeGraph, `codeindex`, Glyphtrail, code graph MCPs | Dependency graph, call graph, structural impact analysis | CodeGraph is PEBRA's required production graph engine; the others are comparators/references |
| AgentMemory + Graphify / CodeGraph pairing | Persistent session memory plus architecture/code graph context | Useful model for "remember the repo before editing"; PEBRA uses CodeGraph for graph facts and owns risk/benefit decisions |
| `sdl-mcp` | Symbol/context governance and access-control style constraints for agents reading code | Related governance idea, but not a change-safety or blast-radius decision system |
| GitHub Copilot Coding Agent validation | Post-generation security and quality validation before finishing a PR | Post-edit validation loop, not pre-edit action selection |
| SonarQube AI Code Assurance, Semgrep, CodeScene | Code quality, security findings, hotspots, critical components, and review prioritization | Scanner/triage systems; useful signals, but not a pre-edit controller |

Unverified repositories or tools should not be listed as evidence providers.

### 16.3 Academic Near-Neighbors

Two recent papers should be cited and distinguished directly because they are close to PEBRA's decision layer.

| Work | Overlap | Difference |
|---|---|---|
| MICE for CATs | Calibrated confidence for tool-using agents; MBR threshold `execute iff p_hat > tau` | Binary execute/abstain setting with coarse utilities; PEBRA generalizes this to multi-action pre-edit decisions and conditions loss on repo evidence, action type, blast radius, and criticality |
| Calibrate-Then-Act | Cost-uncertainty tradeoff; explicit priors; coding task where agents choose whether to test before acting | CTA induces exploration behavior through priors and prompts/RL; PEBRA makes inspect/test/ask decisions through deterministic repo-grounded gates and auditable score provenance |
| Abstain and Validate: A Dual-LLM Policy for Reducing Noise in Agentic Program Repair | Agentic program repair; confidence-style abstention and patch validation | Binary attempt/skip and accept/reject policies; no repo-grounded blast radius, criticality, RAU, inspect/test/ask options, or pre-edit multi-action comparison |
| Uncertainty-Aware, Risk-Adaptive TBAC | Resource criticality plus uncertainty escalation for autonomous agents | Access-control / tool-authorization setting; not code-edit action choice, and no software blast-radius or expected-loss model |

PEBRA should confront these works rather than hide them. They support the thesis that risk-aware autonomy needs uncertainty gates, while leaving room for PEBRA's software-specific integration.

MICE is the closest formal parent for PEBRA's calibrated decision threshold. Its key limitation for this project is that coarse global utilities produce crude decisions; PEBRA's criticality model addresses that gap by conditioning severity on the specific code action and affected region.

CTA supports PEBRA's evidence-before-action framing: when uncertainty is material, the agent should compare the cost of more information against the cost of acting now. PEBRA operationalizes that idea with `inspect_first` and `test_first` decisions before code edits.

### 16.4 Novelty Boundary

PEBRA should not say:

```text
No one has used risk, confidence, or abstention for agents.
```

PEBRA may say:

```text
Prior work gates one axis, one moment, or one binary choice.
PEBRA integrates repo-grounded spread, criticality, calibrated confidence,
expected-loss/RAU scoring, and a five-way pre-edit action enum.
```

---

## 17. Licensing and Tool Notes

### 17.1 Candidate Runtime Tools

| Tool | Use | License / Constraint |
|---|---|---|
| sem | Entity-level diff, blame, impact analysis | MIT OR Apache-2.0 |
| CodeGraph | Multi-language symbol graph, callers/callees, impact, freshness | MIT |
| legacy codeindex | Per-file blast-radius scoring | Apache-2.0 |
| radon | Python LOC, complexity, Halstead, MI | MIT |
| Bandit | Python AST security detection | Apache-2.0 |
| lizard | Multi-language complexity | MIT |
| ast-metrics | Architecture/coupling metrics | MIT |
| MAPIE | Conformal intervals | BSD-3 |
| scikit-learn | Calibration models, Brier score | BSD-3 |
| properscoring | Proper scoring rules | Apache-2.0 |
| AHPy | Pairwise weight elicitation | MIT |
| pymcdm | TOPSIS/VIKOR alternatives | MIT |
| scikit-criteria | MCDA framework | BSD-3 |
| pysensmcda | Ranking sensitivity | MIT |

### 17.2 Runtime and Development Dependencies

PEBRA separates core import purity from package dependencies:

- `pebra.core` must remain stdlib-only so the decision brain is deterministic and auditable.
- The PEBRA package may still ship runtime dependencies used by adapters, calibration, Monte Carlo, config parsing, and signing.

Recommended Python runtime dependencies:

| Dependency | Purpose | License / Constraint |
|---|---|---|
| PyYAML | `.pebra.yml` parsing | MIT |
| radon | Python LOC, complexity, Halstead, Maintainability Index | MIT |
| Bandit | Python AST security detection | Apache-2.0 |
| numpy | Monte Carlo and calibration report math | BSD |
| scikit-learn | Platt/isotonic calibration, logistic stacking | BSD-3 |
| cryptography | Ed25519 signing for signed audit chains | Apache-2.0 / BSD |
| MAPIE | Conformal intervals when enabled by calibration path | BSD-3 |
| FastAPI | local dashboard/API surface for the Risk Observatory | MIT |
| Starlette | local dashboard/API surface for the Risk Observatory | BSD-3 |
| uvicorn | local dashboard server | BSD-3 |
| Jinja2 | dashboard HTML templating | BSD-3 |

Required external runtime tools:

| Tool | Purpose | License / Constraint |
|---|---|---|
| CodeGraph (`@colbymchenry/codegraph`) | Required multi-language symbol graph, call/reference edges, symbol fan-in substrate, and graph freshness signal | MIT; external npm/binary runtime, not a Python package |

PEBRA consumes CodeGraph by subprocess and read-only SQLite, not by importing it into `pebra.core`. The adapter runs `codegraph sync --quiet`, checks `codegraph status --json`, reads `.codegraph/codegraph.db` with Python `sqlite3`, and records CodeGraph package/extraction versions in evidence and calibration scope. CodeGraph supplies graph facts; PEBRA computes fan-in percentiles, edge-confidence tiers, risk/benefit scores, learning, and audit provenance.

External benchmark/comparison tools:

| Tool | Purpose | License / Constraint |
|---|---|---|
| sem | Entity-level diff, blame, impact analysis for comparison/enrichment experiments | MIT OR Apache-2.0 |
| legacy codeindex | Per-file blast-radius scoring / enrichment comparator | Apache-2.0 |

Recommended development dependencies:

| Dependency | Purpose |
|---|---|
| pytest | test runner |
| pytest-cov | coverage reporting |
| ruff | linting and formatting |
| mypy | type checking for ports, dataclasses, and pure core |
| import-linter | enforce the `pebra.core` purity boundary |
| hypothesis | property tests for math invariants |
| syrupy | golden snapshots for worked examples and deterministic flow regression |
| nox | repeatable local/CI test sessions |
| jsonschema | validate request, outcome, guidance, and benchmark fixture shapes |
| playwright | headed visual E2E validation for the Risk Observatory dashboard |
| build | wheel/sdist builds |
| twine | package check/upload |
| pre-commit | local checks before commits |

Recommended benchmark dependency groups:

| Group | Dependencies | Purpose |
|---|---|---|
| `bench` | pandas, scipy, datasets, matplotlib, seaborn | dataset wrangling, scorecard aggregation, reliability diagrams, plots |
| `bench-szz` | pydriller | SZZ/git-mining benchmark labels |
| `bench-agent` | swebench and benchmark agent-runner tooling | SWE-bench-style with-vs-without PEBRA runs |
| `bench-external` | user-provided external comparators | GitNexus, TDAD implementations, commercial agent runners, or other external baselines |

Purity rule: benchmark and dashboard dependencies are forbidden inside `pebra.core`. `pandas`, `scipy`, `matplotlib`, `seaborn`, `datasets`, `pydriller`, `swebench`, `fastapi`, `starlette`, `uvicorn`, and `jinja2` may not be imported by `pebra.core`. `pebra.core` must not import `ports/`, `app/`, `adapters/`, CLI/MCP, dashboard, SQLite, subprocess, or CLI parsing. `pebra.app` may import `core/` and `ports/`, but not concrete adapters; surfaces compose adapters and pass them into use cases. `pebra.dashboard` may read through store/API/scorecard readers, but `pebra.core` and `pebra.adapters` must not import `pebra.dashboard`. Metric primitives that must stay in core should use stdlib only.

GitNexus may be used as an external benchmark comparator, but it is not a runtime dependency and must never be imported, vendored, shipped, or required by PEBRA.

Playwright is a UI/dev dependency only. It verifies dashboard rendering, interactions, screenshots, traces, and console cleanliness against fixture data. It must not be used to validate metric formulas.

### 17.3 Copyleft Avoidance

Do not ship GPL, AGPL, or other strong-copyleft packages as PEBRA runtime dependencies unless the project intentionally accepts those obligations.

`pyDecision` is GPL-3-or-later and must remain reference-only:

- Do not import it.
- Do not vendor it.
- Do not copy or line-by-line translate implementation code.
- Do not use it as a runtime dependency.
- Safe use: high-level method awareness, offline validation, and reference reading with clean-room separation.

Reference clones under `references/` are not runtime dependencies:

- `mice_for_cats` is MIT-licensed but currently contains no usable implementation code.
- `CalibrateThenAct` contains working code but no root license file; treat it as all-rights-reserved/read-only unless the authors add a license.
- `pyDecision` is GPL-3-or-later and remains reference-only.

Implement PEBRA from public formulas, paper descriptions, and permissive libraries. Do not copy code from GPL, unlicensed, or reference-only repositories.

---

## 18. Autonomy Governor Framework

PEBRA's strongest product framing is not generic risk assessment. It is a policy-bound autonomy governor for coding agents.

The core claim:

```text
Autonomous coding agents need external permission logic.
PEBRA decides when an agent may continue without a human,
when it must gather more evidence, and when autonomy must stop.
```

This does not assume agents ignore risk. Modern agents already reason about risk in-context. PEBRA's role is different: make the decision explicit, measurable, consistent, auditable, and project-policy-bound.

### 18.1 Declared Autonomy Envelope

Autonomous action is allowed only inside a declared envelope. The envelope is a containment boundary for cold-start deployments before calibration is mature.

Example envelope:

```yaml
autonomy:
  enabled: true
  allowed_execution_mode: branch_only
  require_pr_before_merge: true
  require_tests_for_autonomous_edit: true
  require_rollback_plan: true
  max_risk_budget_used_percent: 60
  min_edit_confidence: high
  min_rau_band: proceedable
  disallow_criticality_stage: C4
  disallow_actions:
    - dependency_upgrade
    - schema_migration
    - destructive_data_operation
  allowed_decisions_without_human:
    - proceed
    - inspect_first
    - test_first
```

Branch-only and PR-required controls substitute containment for perfect calibration during v1. If the score is imperfect, the action is still isolated, reversible, reviewable, and logged.

### 18.2 Autonomy Decision Loop

```text
agent proposes candidate action
  -> PEBRA gathers repo evidence
  -> PEBRA computes risk_report, RAU band, confidence band, gates
  -> PEBRA checks autonomy envelope
  -> PEBRA renders deterministic model_guidance_packet
  -> if envelope passes:
       proceed with execution_controls + binding guidance
     if evidence is weak:
       inspect_first or test_first
     if risk budget is exceeded or C4 is touched:
       ask_human or reject
  -> agent commits only to a new branch / PR when autonomous
  -> pebra_verify checks final diff against binding guidance
  -> outcome is logged for calibration
```

The decision enum does not change:

```text
proceed | inspect_first | test_first | ask_human | reject
```

Autonomy is expressed through execution controls attached to the decision:

```json
{
  "recommended_decision": "proceed",
  "execution_controls": {
    "autonomy_mode": "contained",
    "execution_target": "new_branch",
    "require_tests_before_commit": true,
    "require_pr_before_merge": true,
    "direct_merge_allowed": false
  },
  "model_guidance_packet": {
    "binding": {
      "safe_scope": {
        "files": ["src/auth.py", "tests/test_auth.py"],
        "symbols": ["validate_login"],
        "edit_policy": "targeted_patch_only"
      },
      "risky_scope": [
        {"change": "dependency upgrades", "action": "requires_reassessment"},
        {"change": "schema changes", "action": "requires_reassessment"}
      ],
      "required_checks_before_commit": ["run tests/test_auth.py"]
    },
    "advisory": {
      "risk_facts": {
        "risk_level": "moderate",
        "risk_budget_used_percent": 50,
        "confidence_percent": 83,
        "code_sensitivity": "C3 auth",
        "affected_area": "low: targeted function and tests"
      },
      "why": [
        "Auth code is sensitive and confidence depends on targeted tests.",
        "If the fix requires dependency or schema changes, the current assessment must be recomputed."
      ]
    }
  }
}
```

### 18.3 Guarded Autonomy Output

A PEBRA response in autonomous mode should explain not only the decision, but also why human approval may be bypassed.

```json
{
  "recommended_decision": "proceed",
  "requires_confirmation": false,
  "risk_report": {
    "headline_risk_percent": 38,
    "risk_type": "risk_budget_indicator",
    "rau": { "value": 0.24, "band": "proceedable" },
    "confidence_percent": 81,
    "confidence_band": "high",
    "why": [
      "Risk budget is below the configured autonomy limit.",
      "Value After Risk is Positive.",
      "Confidence is high after repo evidence gathering.",
      "No C4 path, migration, dependency upgrade, or destructive data operation was detected."
    ]
  },
  "execution_controls": {
    "autonomy_mode": "contained",
    "execution_target": "new_branch",
    "required_branch_prefix": "pebra/",
    "require_tests_before_commit": true,
    "require_pr_before_merge": true,
    "direct_merge_allowed": false
  },
  "model_guidance_packet": {
    "binding": {
      "safe_scope": {
        "files": ["src/auth.py", "tests/test_auth.py"],
        "symbols": ["validate_login"],
        "edit_policy": "targeted_patch_only"
      },
      "risky_scope": [
        {"change": "dependency upgrades", "action": "requires_reassessment"},
        {"change": "schema changes", "action": "requires_reassessment"},
        {"change": "public API changes", "action": "requires_reassessment"},
        {"change": "destructive data operation", "action": "forbidden"}
      ],
      "required_checks_before_commit": ["run tests/test_auth.py"]
    },
    "advisory": {
      "risk_facts": {
        "risk_level": "moderate",
        "risk_budget_used_percent": 38,
        "confidence_percent": 81,
        "code_sensitivity": "C3 auth",
        "affected_area": "low",
        "value_after_risk": "positive"
      },
      "why": [
        "Risk budget is within the autonomy envelope, but auth code remains sensitive.",
        "Keep the edit targeted to validate_login."
      ],
      "safer_alternative": "keep the edit targeted to validate_login"
    }
  }
}
```

If the same action touches `C4` code, exceeds risk budget, lacks tests, or has weak evidence, PEBRA should not silently proceed. It should return `test_first`, `inspect_first`, `ask_human`, or `reject`.

The model guidance packet is the pre-edit face of the same autonomy envelope. `pebra_verify` is the post-edit enforcement step. If the final diff violates binding guidance, the outcome must not be recorded as a successful autonomous edit.

### 18.4 Product Wedge

The MVP should prove one claim:

```text
Agent + PEBRA contained autonomy causes fewer bad autonomous edits
than the same agent deciding alone, without unacceptable friction.
```

The smallest useful experiment:

1. Run the same task set with the base agent alone.
2. Run it again with PEBRA in contained-autonomy mode.
3. Allow autonomous work only on new branches.
4. Require tests and PR before merge.
5. Compare regressions, broad refactors avoided, useful escalations, time cost, and accepted PRs.

Success is not perfect prediction. Success is better autonomous behavior:

- fewer regressions,
- fewer broad unnecessary edits,
- more targeted tests before risky changes,
- useful escalation on high-stakes code,
- complete audit trail for why autonomy was allowed or stopped.

### 18.5 Strategic Boundary

PEBRA should assume platform absorption risk. Agent platforms will keep adding native approval modes, hooks, sandboxing, and post-edit validation.

The defensible standalone lane is vendor-neutral governance:

```text
one policy, one risk report, one audit trail, and one calibration loop
across Codex, Claude Code, Cursor, Copilot, and custom agents.
```

The easy-to-copy part is the envelope rule. The harder-to-copy part is earned calibration from outcomes, project-specific criticality, measured blast radius, and cross-agent audit history. PEBRA should invest there before adding heavy research machinery.

---

## 19. V2 / Research Appendix

These ideas are intentionally out of the v1 runtime path:

- EVPI and EVPPI.
- CEAC curves over risk tolerance.
- Full PSA over risk tolerance and model structure.
- ICER-style pairwise ratios beyond diagnostic display.
- Markov-style maintainability trajectory models over states such as healthy, degraded, legacy, and unmaintainable.
- DEMATEL-style configured covariance mapping.
- Advanced MCDA validation or long-tail ranking methods.
- Odds-ratio calibration of criticality from incident history.
- Survival/hazard-ratio models for time-to-incident only when enough data exists.
- Typed scope/action DAG implementation for learned-fact subsumption and provenance edges, ratified as AD-21 / Phase 7.

NMB-style net-benefit ranking is part of AD-28 and may be used in v1 because it is the same linear family as expected utility and RAU. Monte Carlo sampling for `P(utility < 0)` and `P(action is best)` is allowed earlier when distribution provenance is fitted or explicitly configured. Full PSA and CEAC remain deferred because they need broader risk-sample definitions and risk-tolerance semantics. Markov-style maintainability trajectories are v2 because they need calibrated transition probabilities over time; v1 uses the simpler exposure-times-effort-delta model.

Any method parameter that affects a gate must carry provenance. Published defaults are still coefficients; they need citation or explicit project policy.

Memory-learning methods stay deterministic in PEBRA. Agent-memory papers may motivate decay, reconciliation, and evaluation, but PEBRA must not adopt RL/GRPO memory policies, embedding retrieval, or LLM-authored scoring changes inside the gate-driving core.

---

## 20. Methods References

PEBRA should cite and implement from public method definitions or permissive libraries.

| Method Family | PEBRA Use | Reference Anchor |
|---|---|---|
| ISPOR MCDA good-practice guidance | Structured criteria, scoring, weight elicitation, sensitivity analysis | https://www.ispor.org/docs/default-source/publications/value-outcomes-spotlight/march-april-2016/valueandoutcomesspotlight_mcda_tfr2-summary.pdf |
| Net benefit / health economic evaluation | Expected utility and risk-tolerance framing | https://www.treeage.com/help/Content/13-Cost-Effectiveness-Analysis/4-Net-Benefits-Calculations.htm |
| ISO/IEC 25010 maintainability | Maintainability sub-characteristics: analyzability, modifiability, testability, modularity, reusability | https://iso25000.com/index.php/en/iso-25000-standards/iso-25010 |
| CISQ Automated Technical Debt | Technical debt principal, interest, and future corrective-maintenance cost framing | https://www.it-cisq.org/standards/technical-debt/ |
| Technical debt interest risk | Interest impact times probability of future change; prioritizing maintainability work by expected future maintenance drag | https://link.springer.com/article/10.1007/s42979-020-00406-6 |
| COCOMO-style maintenance estimation | Maintenance effort driven by changed size, maintainability, understanding, complexity, coupling, tests, and familiarity | https://www.iceaaonline.com/wp-content/uploads/2015/06/SW11-Presentation-Minkiewicz-Technical-Debt.pdf |
| Health-economic Markov models | V2 trajectory model for software decay states such as healthy, degraded, legacy, unmaintainable | https://pmc.ncbi.nlm.nih.gov/articles/PMC7661756/ |
| ISPOR-SMDM uncertainty guidance | Probabilistic uncertainty, EVPI, CEAC, parameter uncertainty | https://www.ispor.org/docs/default-source/resources/outcomes-research-guidelines-index/model_parameter_estimation_and_uncertainty-6.pdf |
| AHP / BWM / SMART / swing weighting | Elicited judgment weights and consistency checks | Cite original/public method definitions in implementation |
| CRITIC / Entropy objective weighting | Fallback objective weights when enough alternatives and variance exist | Cite original/public method definitions in implementation |
| VIKOR-style acceptable advantage | Rank-gap stability fallback when Monte Carlo is unavailable | Cite original/public method definitions in implementation |
| DEMATEL | Offline criterion influence/correlation model for Monte Carlo | Cite original/public method definitions in implementation |
| Fuzzy triangular ranges | Configured uncertainty ranges for judgment inputs | Cite original/public method definitions in implementation |
| MICE for CATs | Calibrated confidence and MBR execute/abstain threshold for tool agents | https://aclanthology.org/2025.naacl-long.615/ |
| Calibrate-Then-Act | Cost-aware exploration, explicit priors, and selective testing before action | https://arxiv.org/abs/2602.16699 |
| Abstain and Validate | Confidence-based abstention and patch validation in agentic program repair | https://arxiv.org/abs/2510.03217 |
| Risk-Adaptive TBAC | Risk plus uncertainty escalation for autonomous agent access control | https://arxiv.org/abs/2510.11414 |
| Memory for Autonomous LLM Agents survey | Write-manage-read memory loop, trustworthy reflection, learned forgetting, evaluation gaps | https://arxiv.org/abs/2603.07670 |
| SAGE | Ebbinghaus-style forgetting and memory optimization for self-evolving agents | https://arxiv.org/abs/2409.00872 |
| SSGM | Governed memory, temporal grounding, contradiction checks, reconciliation | https://arxiv.org/abs/2603.11768 |
| Evo-Memory | Streaming test-time learning and memory-evolution evaluation | https://arxiv.org/abs/2511.20857 |
| Experiential Reflective Learning | Experience-derived heuristics and selective reuse across tasks | https://arxiv.org/abs/2603.24639 |
| Trainable Graph Memory | Structured memory and strategy reuse; use only as reference, not RL memory policy | https://arxiv.org/abs/2511.07800 |
| OWASP Risk Rating | Security likelihood, technical impact, and business impact framing | https://owasp.org/www-community/OWASP_Risk_Rating_Methodology |
| CVSS v4.0 | Vulnerability severity, threat, environmental, and supplemental metrics | https://www.first.org/cvss/v4.0/specification-document |
| CISA SSVC | Decision-oriented vulnerability prioritization | https://www.cisa.gov/stakeholder-specific-vulnerability-categorization-ssvc |
| MITRE CWE / CAPEC / ATT&CK | Weakness, attack-pattern, and adversary-behavior vocabulary | https://cwe.mitre.org/ and https://capec.mitre.org/ |
| Logistic / odds-ratio calibration | Outcome-calibrated criticality and incident-risk multipliers | Use public statistical definitions or permissive libraries |
| Logarithmic opinion pool / log-linear pooling | Top-k probability composition for matching learned facts | Cite public method definitions in implementation |
| Longest-prefix / specificity matching | Deterministic scope precedence for learned facts | Use public routing/CSS specificity definitions as implementation notes |
| AgentMemory | Session memory, structured facts, hybrid BM25/vector/graph retrieval, and pairing with code graph tools | https://github.com/rohitg00/agentmemory |
| Graphify / architecture anchors | Example of local architecture anchors, degree, and bridge-degree summaries | Benchmark/reference comparison only |
| CodeGraph | Required local symbol graph, call/reference edges, impact queries, and freshness status | MIT; production graph backend, PEBRA owns scoring/learning |
| legacy codeindex | SQLite code structure graph, symbol index, and blast-radius impact | Benchmark/reference comparator only |
| Inverse-variance weighting | Precision-weighted evidence aggregation | https://www.nist.gov/document/combine-1pdf |
| WGCNA | Weighted graph propagation and soft adjacency over dependency graphs | https://link.springer.com/article/10.1186/1471-2105-9-559 |
| Probability calibration | Calibrated `p_success` and event probabilities | https://scikit-learn.org/stable/modules/calibration.html |
| Learning to rank | Later learned ranking from outcomes | https://xgboost.readthedocs.io/en/latest/tutorials/learning_to_rank.html |
| Radon | Python LOC, cyclomatic complexity, Halstead, MI | https://github.com/rubik/radon |
| Bandit | Python AST-based security issue detection | https://github.com/PyCQA/bandit |
| AST Metrics | Architecture, coupling, complexity, maintainability | https://github.com/Halleck45/ast-metrics |
| Lizard | Multi-language cyclomatic complexity analyzer | https://github.com/terryyin/lizard |
| McCabe complexity thresholds | Complexity risk bands | https://support.scitools.com/support/solutions/articles/70000582297-understanding-mccabe-cyclomatic-complexity |
