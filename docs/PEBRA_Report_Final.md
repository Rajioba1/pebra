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
radon | sem | bandit | ast_import_graph | .pebra.yml | outcome_store | model | user | criticality_token_prior
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
| `risk_budget_used_percent` | Risk Level | How close this edit is to the configured safety limit |
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
Affected Area
Code Sensitivity
Confidence
Value After Risk
Why
Required Guardrails
```

Technical details such as raw RAU, expected-loss formulas, event probabilities, and provenance remain available in JSON or an explicit math/details view.

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
| Module import fan-in | Many modules import this module | import graph in-degree |
| Module import fan-out | This file imports many modules | import graph out-degree |
| Symbol import fan-in | Many files import a specific function/class | AST import resolution |
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
  high     if fan_in >= 10 or fan_in_percentile >= 0.90
  moderate if fan_in >= 3  or fan_in_percentile >= 0.50
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

### 4.3 Evidence Escalation Ladder

Use repo-local evidence first. Escalate only when local evidence is insufficient.

1. Local repo evidence: code, imports, tests, git history, call graph, dependency graph, project config.
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

Use max aggregation because a single critical file can dominate risk.

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
disutility_j = max(elicited_disutility_j, d_prior)

expected_loss(a) = sum_j p_event_j(a) * disutility_j
```

The criticality stage supplies a disutility floor, not a multiplier. `p_event_j` remains the likelihood channel and should be driven by codebase evidence such as usage counts, blast radius, tests, changed APIs, and structural signals. The raw C-stage is never multiplied.

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
| `migration_failure` | migration flag, schema change, rollback plan, migration history | MCDA elicitation |
| `dependency_break` | dependency change, lockfile size, semver level, changelog/advisory signals | MCDA elicitation |
| `external_state_damage` | network use, DB writes, filesystem writes, external API writes | MCDA elicitation |
| `security_sensitive_change` | critical path, SAST findings, secret/crypto/shell/SQL patterns | MCDA elicitation |

Review burden is not an adverse event by default. It is subtracted separately as `review_cost`. Only model it as an adverse event if there is a separate downstream failure, such as review delay causing missed release risk.

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

```text
if action violates policy:
    reject

if criticality_stage == C4
and thresholds.c4_always_ask_human:
    requires_confirmation = thresholds.c4_requires_confirmation
    ask_human

if criticality_stage == C3:
    max_expected_loss_limit = min(
      thresholds.max_expected_loss_without_human,
      thresholds.c3_max_expected_loss_without_human
    )
    requires_confirmation = thresholds.c3_requires_confirmation
else:
    max_expected_loss_limit = thresholds.max_expected_loss_without_human

if expected_loss > max_expected_loss_limit:
    ask_human or reject

if monte_carlo_gate_available
and P(utility < 0) > thresholds.max_p_negative_utility:
    ask_human or reject

if not monte_carlo_gate_available
and utility_sd > thresholds.max_utility_sd_without_human
and expected_utility > 0:
    ask_human

if monte_carlo_gate_available
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
```

Criticality affects gates only through this section. Section 5 may describe gate pressure, but Section 8 is the sole decision authority.

Double-count guard:

```text
criticality_stage -> disutility floor and threshold modifiers
count/blast_radius/usage -> p_event

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
  "recommended_decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
  "decision_reason": "Patch action has positive RAU after evidence, but confidence upgraded from low so confirmation is required.",
  "risk_report": {},
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
  "scores": {},
  "edit_control": {}
}
```

`recommended_decision` is the top-level decision for the selected action. Per-action `decision` records how each candidate was classified during comparison.

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
- Weakest edit-confidence factor.

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
    "RAU 0.31 is positive after the uncertainty penalty and is in the proceedable band.",
    "Confidence is 83% after repo evidence gathering.",
    "Auth code is C3, so confirmation is required."
  ]
}
```

Default human-readable rendering:

```text
PEBRA Decision: Proceed, but confirm first

Risk Level: Moderate
Affected Area: Low
Code Sensitivity: High
Confidence: High
Value After Risk: Positive

Why:
- This touches auth-related code, so mistakes have higher impact.
- The planned edit is small and reversible.
- Local call-site search found limited usage.
- A targeted auth test exists.

Required Guardrails:
- Make the smallest sufficient patch.
- Run the targeted auth test before finalizing.
- Commit on a new branch if running autonomously.
```

Worked example values must be computed from stated formulas, not manually invented. A future docs check should parse examples and fail if derived values drift.

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
  "recommended_decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
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
      "RAU 0.31 is positive after the uncertainty penalty and is in the proceedable band.",
      "Confidence is 83% after repo evidence gathering.",
      "Auth code is C3, so confirmation is required."
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
Agent / CLI
   |
   v
PEBRA MCP server
   |
   +-- Request/schema validator
   +-- Candidate action parser
   +-- Decision query validator
   +-- Evidence collector
   |     +-- git diff/status
   |     +-- structural metrics
   |     +-- import graph signals
   |     +-- call/dependency graph signals
   |     +-- git history/churn
   |     +-- security static analysis
   |     +-- test discovery
   |     +-- repo config
   |
   +-- Assessment builder
   +-- Score normalizer
   +-- Weight resolver
   +-- Confidence gate
   +-- Decision engine
   +-- Explanation generator
   +-- Outcome logger
   +-- Calibration store
```

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
  "gates": {},
  "decision": null,
  "provenance": {}
}
```

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

thresholds:
  max_expected_loss_without_human: 0.45
  c3_max_expected_loss_without_human: 0.20
  c3_requires_confirmation: true
  c4_always_ask_human: true
  c4_requires_confirmation: true
  max_p_negative_utility: 0.10
  max_utility_sd_without_human: 0.20
  decision_instability_threshold: 0.10
  min_monte_carlo_sample_count: 10000
  high_edit_confidence: 0.75
  low_edit_confidence: 0.50
  max_retrieval_only_confidence: 0.90
  require_evidence_delta_for_low_confidence_upgrade: true
  require_user_confirmation_for_low_confidence_upgrade: true
  medium_auto_proceed_requires:
    - targeted_checks_pass
    - residual_blast_radius_low
    - no_policy_violation

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

preferred_blast_radius_tool: sem

evidence:
  file_size:
    high_loc: 1000
    critical_loc: 3000
    high_percentile: 0.90
    critical_percentile: 0.95
  fan_in:
    moderate_absolute: 3
    high_absolute: 10
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

Outcome logging is v1 schema-only unless the implementation ships `pebra_record_outcome`. Calibration reports require stored outcomes.

---

## 13. MVP Scope and Build

### 13.1 MCP and CLI

v1 should include:

- MCP tool `pebra_compare`.
- Optional CLI command `pebra assess`.
- Optional convenience wrapper `pebra_assess` for a single action.
- JSON input/output using `schema_version: "0.1"`.
- Human-readable table generated from canonical response.

Roadmap:

- `pebra_explain`.
- `pebra_record_outcome`.

### 13.2 v1 Should Include

- Canonical request and response schemas.
- Decision enum and state machine.
- Tier-1 evidence discovery:
  - LOC and complexity via `radon`.
  - Python AST import graph fan-in/fan-out.
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
- Outcome logging schema.

### 13.3 v1.5 Should Add

- Multi-language import/dependency adapters.
- Call graph adapters beyond the first supported language.
- Maintainability Index and coverage mapping where tools are available.
- Objective weights such as CRITIC/Entropy when candidate count and criterion variance are sufficient.
- Configured triangular ranges for judgment-input uncertainty.
- Method sensitivity report for weight/rank stability.
- Monte Carlo decision gates when validated distributions or calibrated outcome data exist.

### 13.4 v1 Should Not Include

- Full new code graph engine.
- Full multi-language evidence discovery.
- Generic MCDA method catalogue or MCDA studio UI.
- Runtime EVPI, EVPPI, CEAC, or PSA.
- Automatic edits.
- Claims of universal correctness.
- Risk labels used as direct model inputs without measured evidence.
- Broad vendor-specific integrations before the core loop works.

### 13.5 Success Criteria

PEBRA v1 is useful if:

- It makes agents choose narrower edits when broad edits have poor expected value.
- It discovers structural risk from measured repo signals.
- It recommends tests/inspection when uncertainty could change the decision.
- It avoids overconfident action when confidence is low.
- It produces rationales humans can audit.
- It integrates through MCP without changing the agent runtime.

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

---

## 15. Open Design Questions

1. Should v1 be MCP-first, CLI-first, or both?
2. Should PEBRA generate candidate actions or only score actions supplied by the agent?
3. Which blast-radius provider should be the default?
4. Should risk tolerance be a single number or per-directory policy?
5. Should outcome logging be local-only by default?
6. Which first agent should PEBRA target: Codex, Claude Code, Cursor, or any MCP client?

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
| Decision math | Expected loss, RAU, confidence gates, and optional Monte Carlo gates |
| Auditability | Provenance on scores, distributions, and evidence actions |

### 16.2 GitHub and Platform Neighbors

Blast-radius and code graph tools are useful evidence providers for PEBRA, but they should not be described as equivalent systems.

| Neighbor | What It Covers | Distinction From PEBRA |
|---|---|---|
| `code-impact-mcp` | MCP-style code impact / blast-radius gate such as pass, warn, or block | Single-axis impact gate; no benefit, criticality, RAU, confidence state machine, or five-way action enum |
| `Ctxo` | Repo context, dependency information, and safe-edit style guardrails | Primarily context and edit-safety support; not an expected-loss decision controller |
| `codeindex`, `Glyphtrail`, code graph MCPs | Dependency graph, call graph, structural impact analysis | Evidence providers; they estimate spread, not full action utility |
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
| codeindex | Per-file blast-radius scoring | Apache-2.0 |
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

### 17.2 Copyleft Avoidance

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
  -> if envelope passes:
       proceed with execution_controls
     if evidence is weak:
       inspect_first or test_first
     if risk budget is exceeded or C4 is touched:
       ask_human or reject
  -> agent commits only to a new branch / PR when autonomous
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
      "RAU is positive and proceedable.",
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
  }
}
```

If the same action touches `C4` code, exceeds risk budget, lacks tests, or has weak evidence, PEBRA should not silently proceed. It should return `test_first`, `inspect_first`, `ask_human`, or `reject`.

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
- ICER and NMB comparisons beyond v1 expected utility.
- DEMATEL-style configured covariance mapping.
- Advanced MCDA validation or long-tail ranking methods.
- Odds-ratio calibration of criticality from incident history.
- Survival/hazard-ratio models for time-to-incident only when enough data exists.

Monte Carlo sampling for `P(utility < 0)` and `P(action is best)` is allowed earlier when distribution provenance is fitted or explicitly configured. Full PSA and CEAC remain deferred because they need broader risk-sample definitions and risk-tolerance semantics.

Any method parameter that affects a gate must carry provenance. Published defaults are still coefficients; they need citation or explicit project policy.

---

## 20. Methods References

PEBRA should cite and implement from public method definitions or permissive libraries.

| Method Family | PEBRA Use | Reference Anchor |
|---|---|---|
| ISPOR MCDA good-practice guidance | Structured criteria, scoring, weight elicitation, sensitivity analysis | https://www.ispor.org/docs/default-source/publications/value-outcomes-spotlight/march-april-2016/valueandoutcomesspotlight_mcda_tfr2-summary.pdf |
| Net benefit / health economic evaluation | Expected utility and risk-tolerance framing | https://www.treeage.com/help/Content/13-Cost-Effectiveness-Analysis/4-Net-Benefits-Calculations.htm |
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
| OWASP Risk Rating | Security likelihood, technical impact, and business impact framing | https://owasp.org/www-community/OWASP_Risk_Rating_Methodology |
| CVSS v4.0 | Vulnerability severity, threat, environmental, and supplemental metrics | https://www.first.org/cvss/v4.0/specification-document |
| CISA SSVC | Decision-oriented vulnerability prioritization | https://www.cisa.gov/stakeholder-specific-vulnerability-categorization-ssvc |
| MITRE CWE / CAPEC / ATT&CK | Weakness, attack-pattern, and adversary-behavior vocabulary | https://cwe.mitre.org/ and https://capec.mitre.org/ |
| Logistic / odds-ratio calibration | Outcome-calibrated criticality and incident-risk multipliers | Use public statistical definitions or permissive libraries |
| Inverse-variance weighting | Precision-weighted evidence aggregation | https://www.nist.gov/document/combine-1pdf |
| WGCNA | Weighted graph propagation and soft adjacency over dependency graphs | https://link.springer.com/article/10.1186/1471-2105-9-559 |
| Probability calibration | Calibrated `p_success` and event probabilities | https://scikit-learn.org/stable/modules/calibration.html |
| Learning to rank | Later learned ranking from outcomes | https://xgboost.readthedocs.io/en/latest/tutorials/learning_to_rank.html |
| Radon | Python LOC, cyclomatic complexity, Halstead, MI | https://github.com/rubik/radon |
| Bandit | Python AST-based security issue detection | https://github.com/PyCQA/bandit |
| AST Metrics | Architecture, coupling, complexity, maintainability | https://github.com/Halleck45/ast-metrics |
| Lizard | Multi-language cyclomatic complexity analyzer | https://github.com/terryyin/lizard |
| McCabe complexity thresholds | Complexity risk bands | https://support.scitools.com/support/solutions/articles/70000582297-understanding-mccabe-cyclomatic-complexity |
