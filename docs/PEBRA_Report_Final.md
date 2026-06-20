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

When confidence falls, the agent should not guess harder. It should gather better evidence, reduce edit scope, or ask for help.

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
  "decision": "proceed",
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
radon | sem | bandit | ast_import_graph | .pebra.yml | outcome_store | model | user
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
| `medium` | `0.50 to < 0.75` | Tighten scope, inspect/test, then re-score |
| `high` | `>= 0.75` | Edit may proceed if gates pass |

### 2.6 Score Levels

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

Criticality comes from project policy or structured elicitation.

For actions touching multiple files:

```text
criticality(action) = max(criticality(file) for file in expected_files)
```

Use max aggregation because a single critical file can dominate risk.

### 5.5 Adverse Event Model

Expected loss uses adverse-event probabilities and disutilities:

```text
p_event_j = event_model_j(features)
disutility_j = MCDA_disutility_j

expected_loss(a) = sum_j p_event_j(a) * disutility_j
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

Configured correlations may be supplied offline through structured expert influence mapping:

```text
blast_radius -> p_success: negative influence
blast_radius -> review_cost: positive influence
criticality -> disutility: positive influence
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
  p_success: 0.1667
  evidence_quality: 0.1667
  testability: 0.1667
  reversibility: 0.1667
  source_reliability: 0.1667
  scope_control: 0.1667
```

The implementation may override these through `.pebra.yml` if provenance is stored.

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

### 8.2 Hard Gates

Gate names must map directly to `.pebra.yml`.

```text
if action violates policy:
    reject

if expected_loss > thresholds.max_expected_loss_without_human:
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
  "decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
  "decision_reason": "Patch action has positive RAU after evidence, but confidence upgraded from low so confirmation is required.",
  "actions": [],
  "thresholds_used": {},
  "evidence_delta": {},
  "provenance": {}
}
```

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

Because confidence upgraded from low to high, the response uses `decision: "proceed"` and `requires_confirmation: true`.

### 10.3 Canonical Response Example

```json
{
  "schema_version": "0.1",
  "task": "Fix failing login validation",
  "decision": "proceed",
  "recommended_action_id": "a1",
  "requires_confirmation": true,
  "decision_reason": "Repo-local evidence reduced uncertainty; targeted patch has positive RAU.",
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
              "disutility_source_type": "elicited"
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
    "max_p_negative_utility": 0.10,
    "max_utility_sd_without_human": 0.20,
    "decision_instability_threshold": 0.10,
    "high_edit_confidence": 0.75,
    "low_edit_confidence": 0.50
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
  "src/auth/**": 1.0
  "src/payments/**": 1.0
  "src/migrations/**": 0.95
  "src/ui/**": 0.45
  "tests/**": 0.20
  "docs/**": 0.10

thresholds:
  max_expected_loss_without_human: 0.45
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

edit_confidence_weights:
  p_success: 0.1667
  evidence_quality: 0.1667
  testability: 0.1667
  reversibility: 0.1667
  source_reliability: 0.1667
  scope_control: 0.1667

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

## 16. Licensing and Tool Notes

### 16.1 Candidate Runtime Tools

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

### 16.2 Copyleft Avoidance

Do not ship GPL, AGPL, or other strong-copyleft packages as PEBRA runtime dependencies unless the project intentionally accepts those obligations.

`pyDecision` is GPL-3-or-later and must remain reference-only:

- Do not import it.
- Do not vendor it.
- Do not copy or line-by-line translate implementation code.
- Do not use it as a runtime dependency.
- Safe use: high-level method awareness, offline validation, and reference reading with clean-room separation.

---

## 17. V2 / Research Appendix

These ideas are intentionally out of the v1 runtime path:

- EVPI and EVPPI.
- CEAC curves over risk tolerance.
- Full PSA over risk tolerance and model structure.
- ICER and NMB comparisons beyond v1 expected utility.
- DEMATEL-style configured covariance mapping.
- Advanced MCDA validation or long-tail ranking methods.

Monte Carlo sampling for `P(utility < 0)` and `P(action is best)` is allowed earlier when distribution provenance is fitted or explicitly configured. Full PSA and CEAC remain deferred because they need broader risk-sample definitions and risk-tolerance semantics.

Any method parameter that affects a gate must carry provenance. Published defaults are still coefficients; they need citation or explicit project policy.

---

## 18. Methods References

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
| Inverse-variance weighting | Precision-weighted evidence aggregation | https://www.nist.gov/document/combine-1pdf |
| WGCNA | Weighted graph propagation and soft adjacency over dependency graphs | https://link.springer.com/article/10.1186/1471-2105-9-559 |
| Probability calibration | Calibrated `p_success` and event probabilities | https://scikit-learn.org/stable/modules/calibration.html |
| Learning to rank | Later learned ranking from outcomes | https://xgboost.readthedocs.io/en/latest/tutorials/learning_to_rank.html |
| Radon | Python LOC, cyclomatic complexity, Halstead, MI | https://github.com/rubik/radon |
| Bandit | Python AST-based security issue detection | https://github.com/PyCQA/bandit |
| AST Metrics | Architecture, coupling, complexity, maintainability | https://github.com/Halleck45/ast-metrics |
| Lizard | Multi-language cyclomatic complexity analyzer | https://github.com/terryyin/lizard |
| McCabe complexity thresholds | Complexity risk bands | https://support.scitools.com/support/solutions/articles/70000582297-understanding-mccabe-cyclomatic-complexity |

