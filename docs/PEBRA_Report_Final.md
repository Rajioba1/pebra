# PEBRA: Pre-Edit Benefit-Risk Assessment Tool for Coding Agents

## Tool-Oriented Technical Spec

*Updated June 2026. This document reframes PEBRA as a practical agent tool, not an academic paper. License notes are technical planning notes, not legal advice.*

---

## 1. Product Thesis

PEBRA is a pre-edit decision tool for coding agents.

Before an agent edits code, it should compare the available actions and answer:

> Is this edit worth doing now, or should the agent inspect more, run tests, ask a human, or choose a narrower change?

PEBRA gives the agent a benefit-risk table with numeric scores for each candidate action. The tool does not replace sandboxing, code review, CI, or policy enforcement. It runs before editing and helps the agent choose a safer, higher-value next step.

The core product is simple. This is an abbreviated display; the canonical JSON schema is in Section 9.2.

```text
Task: Fix failing login validation.

Candidate actions:

| Action                  | Benefit | P(success) | Expected Loss | RAU   | Confidence | Decision    |
|-------------------------|---------|------------|---------------|-------|------------|-------------|
| Inspect auth tests      | --      | --         | 0.02          | --    | --         | Do first    |
| Patch validate_login    | 0.82    | 0.74       | 0.10          | 0.31  | High after evidence | Proceed with confirmation |
| Refactor auth module    | 0.88    | 0.41       | 0.58          | -0.70 | Low        | Reject      |
| Upgrade auth dependency | 0.63    | 0.48       | 0.49          | -0.54 | Low        | Ask human   |
```

The agent can then continue with the chosen action and attach PEBRA's rationale to its audit trail.

PEBRA should be understood as a confidence-based editing controller, not only a risk scorer. When confidence falls, the agent should not guess harder; it should gather better evidence, reduce edit scope, or ask for help.

---

## 2. What PEBRA Is

PEBRA is:

- An MCP server agents can call before code edits.
- A CLI developers can run locally.
- An evidence discovery layer that measures repo structure before scoring risk.
- A scoring layer that compares candidate actions.
- A confidence gate that decides whether to edit now, gather evidence, shrink scope, or ask a human.
- A normalization layer over existing blast-radius and code-risk tools.
- A learning loop that records outcomes and calibrates future scores.

PEBRA is not:

- A replacement for tests or CI.
- A replacement for sandboxing.
- A general policy engine.
- A new blast-radius analyzer from scratch.
- A claim that every score is a true probability.

Important distinction:

- `P(success)` is a calibrated probability target.
- `benefit`, `blast_radius`, `criticality`, `reversibility`, `testability`, `review_cost`, and uncertainty estimates are normalized Level 1 scores from 0 to 1.
- `expected_loss`, `expected_utility`, and `risk_adjusted_utility` are Level 2 derived scores computed from Level 1 evidence.
- `edit_confidence` is a Level 2 controller score that combines success probability, evidence quality, testability, reversibility, source reliability, and edit scope.
- The UI may display these as percentages, but internally they are evidence-backed scalar scores.
- Every score should carry provenance: value, source type, confidence, and evidence.
- PEBRA should not ship unexplained magic coefficients. Weights must come from published methods: MCDA elicitation, inverse-variance precision weighting, calibrated ML, graph propagation, or explicit project risk policy.

---

## 3. The Gap PEBRA Fills

Existing tools already cover many pieces:

| Category | Existing Tools | What They Do |
|---|---|---|
| Authorization and audit | OAP / APort, AEGIS | Decide whether a tool call is allowed, blocked, or pending |
| Unsafe tool-call detection | ToolSafe | Detect unsafe agent tool use before execution |
| Blast radius and impact | sem, GitNexus, codeindex, inspect, TDAD | Estimate what code/tests/entities may be affected |
| PR review | CodeRabbit, Greptile, Qodo, GitHub Code Quality | Review diffs or pull requests after code exists |
| Uncertainty | UQLM / CodeGenUQ, MAPIE, Uncertainty Toolbox | Estimate confidence, calibration, or uncertainty intervals |

The missing layer is the comparator:

> Given several permissible coding actions, which action has the best risk-adjusted expected utility after accounting for benefit, success probability, adverse-event loss, review cost, and uncertainty?

That is PEBRA's job.

---

## 4. Methodological Foundation

PEBRA's novelty is the coding-agent application, not the invention of new scoring math. The decision process should be built from published, citable methods.

| PEBRA Need | Principle-Based Method | Role in PEBRA |
|---|---|---|
| Rank alternatives under multiple criteria | MCDA / MCDM, including AHP, SMART/SMARTER, swing weighting, DCE/conjoint | Elicit benefit and disutility weights rather than hardcoding them |
| Convert benefit and cost into one score | Net benefit / expected utility from pharmacoeconomics and health decision analysis | Compare actions using effectiveness minus risk/cost |
| Combine unequal-quality evidence | Inverse-variance / precision weighting | Give more influence to measured or lower-variance evidence sources |
| Estimate action success probability | Calibrated ML probability models | Produce `p_success` values whose frequencies match observed outcomes |
| Penalize uncertainty | Lower confidence bound / risk-adjusted utility | Prefer actions whose expected utility remains positive under uncertainty |
| Gate retrieval and edit scope | Confidence-gated retrieval and minimum sufficient edit policy | Use local/docs/GitHub/web evidence only when confidence requires it, and shrink edits as confidence falls |
| Propagate dependency risk | Weighted graph methods; WGCNA-style soft adjacency as an analogy | Convert code dependency graphs into continuous blast-radius influence |
| Decide whether to gather information | EVPI / EVPPI in v2 | Recommend inspection or tests when uncertainty reduction has value |

The v1 runtime should use only the parts that can be implemented and explained cleanly:

```text
1. MCDA-derived benefit and disutility scales.
2. Calibrated probabilities for success and adverse coding events.
3. Expected-loss model: probability * disutility.
4. Inverse-variance evidence aggregation when multiple estimates exist.
5. Risk-adjusted utility using a lower confidence bound.
```

This makes PEBRA's decision math auditable and defensible: each coefficient is either elicited, learned, variance-weighted, or declared as project policy.

---

## 5. User Workflow

### 5.1 Agent Workflow

1. User asks agent to complete a coding task.
2. Agent proposes 2 to 6 candidate actions.
3. Agent calls PEBRA before editing.
4. PEBRA scores each action.
5. PEBRA assigns an edit-confidence band and evidence requirement.
6. PEBRA returns one of five decisions:
   - `proceed`
   - `inspect_first`
   - `test_first`
   - `ask_human`
   - `reject`
7. Agent follows the decision and edit policy.
8. After the task, agent records the outcome so PEBRA can calibrate future scores.

### 5.2 Developer Workflow

```bash
pebra assess --task "Fix failing login validation" --actions actions.json
pebra compare --task task.md --actions actions.json --format table

# Roadmap after v1
pebra record-outcome --assessment assessment.json --outcome outcome.json
```

### 5.3 MCP Workflow

PEBRA v1 should expose one required MCP tool and may add roadmap tools later:

| MCP Tool | Purpose |
|---|---|
| `pebra_compare` | Required in v1. Score and rank several candidate actions |
| `pebra_assess` | Optional v1 convenience wrapper for scoring one candidate action |
| `pebra_explain` | Roadmap. Return the rationale and evidence behind a score |
| `pebra_record_outcome` | Roadmap. Store actual outcome for calibration |

---

## 6. Candidate Action Model

An action is not just a sentence. It should include expected files, edit type, intended outcome, and reversibility.

```json
{
  "task": "Fix failing login validation",
  "candidate_actions": [
    {
      "id": "a1",
      "label": "Patch validate_login only",
      "intent": "Fix the failing login validation behavior with a targeted function patch.",
      "expected_files": ["src/auth.py", "tests/test_auth.py"],
      "edit_type": "targeted_patch",
      "requires_dependency_change": false,
      "requires_schema_change": false,
      "requires_network": false,
      "requires_migration": false,
      "writes_external_state": false,
      "rollback_plan": "git restore changed files",
      "test_plan": "run tests/test_auth.py"
    }
  ]
}
```

PEBRA should reject or downrank vague actions such as:

```text
Fix the auth module.
Refactor login.
Improve everything.
```

The action must be concrete enough to score.

---

## 7. Evidence Discovery and Scoring Dimensions

### 7.1 Evidence Discovery Layer

PEBRA must not assign adverse-event probabilities from risk labels alone. Labels such as `migration_failure`, `dependency_break`, `public_api_break`, and `security_sensitive_change` are event classes, not evidence.

Before the math runs, PEBRA should inspect the repository and produce measured software-engineering signals:

```text
Candidate action
  -> Evidence Discovery Layer
      -> file size signals
      -> import graph signals
      -> call graph signals
      -> dependency graph signals
      -> complexity signals
      -> maintainability signals
      -> git history / hotspot signals
      -> test coverage signals
      -> side-effect signals
      -> package / dependency signals
      -> security static-analysis signals
      -> external advisory signals when needed
  -> adverse-event probability models
  -> expected loss
  -> edit confidence
  -> risk-adjusted utility decision
```

Local evidence should be preferred:

1. **Repo-local analysis first:** AST imports, import graph, call graph, dependency graph, git history, tests, package files, lockfiles.
2. **Agent semantic classification second:** use the coding model to classify intent or ambiguous project conventions only when local structure is insufficient.
3. **User query third:** ask when project-specific knowledge materially changes the decision.
4. **Online/GitHub checks last:** use for dependency changelogs, advisories, release notes, public API docs, and ecosystem risk.

The output of this layer is structured evidence, not prose:

```json
{
  "signal": "call_graph_fan_in",
  "value": 14,
  "risk_band": "high",
  "normalization": "repo_percentile",
  "percentile": 0.93,
  "source": "sem",
  "confidence": 0.84,
  "evidence": ["validate_login has 14 inbound call/dependency references."]
}
```

Adverse event probabilities should consume these signals:

```text
p_event(public_api_break) =
  calibrated_model(
    exported_symbol_changed,
    call_graph_fan_in_percentile,
    dependency_depth,
    public_api_surface,
    dependent_tests_missing,
    historical_api_break_rate
  )
```

### 7.2 Structural Risk Signals

PEBRA should combine absolute thresholds with repo-relative percentiles. Absolute thresholds capture known engineering limits; percentiles adapt to each codebase.

| Signal | Why It Matters | Primary Algorithm / Tool |
|---|---|---|
| File LOC / logical LOC | Monolith files are harder to understand and review | raw metrics, percentile outlier detection, `radon`, `ast-metrics`, `lizard` |
| Module import fan-in | Many files import this module, so module-level changes have broad reach | import graph in-degree, `sem`, `ast-metrics`, language import parsers |
| Module import fan-out | This file imports many modules, so the edit has more integration context | import graph out-degree, instability, dependency-cruising tools |
| Symbol import fan-in | Many files import a specific function/class/symbol, so symbol changes are public-ish | AST import resolution, exported symbol map, language server/compiler API |
| Star imports | `from x import *` hides dependencies and weakens static analysis | AST import pattern detection |
| Dynamic imports | `importlib`, dynamic `require`, lazy imports can hide runtime edges | AST/string pattern detection, runtime trace when available |
| Circular imports | Import cycles make initialization and refactors fragile | strongly connected components in import graph |
| Third-party import changes | New/removed external imports imply dependency/security/release risk | package diff, import diff, lockfile diff |
| Import boundary violations | Layering violations indicate architecture drift | import-linter/dependency-cruiser rules, project policy |
| Function or module fan-in | More callers means broader breakage if behavior changes | call graph in-degree, dependency graph fan-in, `sem`, `ast-metrics` |
| Fan-out / instability | Many outgoing dependencies increase context and integration risk | fan-in/fan-out, instability `I = fan_out / (fan_in + fan_out)` |
| Cyclomatic complexity | More independent paths require more tests and increase change risk | McCabe complexity, `radon`, `lizard`, `ast-metrics` |
| Maintainability Index | Composite structural health from SLOC, complexity, Halstead volume | `radon`, `ast-metrics` |
| Git churn and bug density | Frequently changed, defect-prone files are hotspots | git history, churn x complexity hotspot analysis |
| Test coverage of touched code | Missing tests raises uncertainty and regression risk | coverage mapping, test selection, TDAD-style affected tests |
| Public/exported API changes | Downstream callers can break even if local tests pass | AST export diff, symbol visibility, package surface analysis |
| Dependency or lockfile changes | Dependency upgrades can break through transitive behavior | package diff, semver change, changelog/advisory lookup |
| Migration/schema changes | Data changes are harder to reverse than code changes | path/type detection, ORM migration detection |
| Security-sensitive operations | Shell, SQL, deserialization, crypto, secrets increase security risk | Bandit for Python, Semgrep or language-specific SAST tools |

Default risk bands should be expressed as both absolute and relative rules:

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

The exact thresholds must be provenance-bearing policy defaults. PEBRA should report whether a band came from a published fixed threshold, a repo percentile, or local project policy.

### 7.3 Two-Level Score Model

PEBRA uses a two-level model.

Level 1 scores are raw evidence or direct estimates:

| Level 1 Score | Range | Meaning | Primary Evidence |
|---|---:|---|---|
| `benefit` | 0 to 1 | Value if the action succeeds | Task alignment, failing tests, user priority, severity |
| `p_success` | 0 to 1 | Calibrated probability the action succeeds | CodeGenUQ/UQLM features, static checks, historical outcomes |
| `blast_radius` | 0 to 1 | Scope of possible damage if wrong | sem/codeindex/GitNexus/inspect impact output |
| `criticality` | 0 to 1 | Business or safety importance of touched code | project config, path labels, ownership metadata |
| `reversibility` | 0 to 1 | Ease of undoing the action | git rollback, migrations, data writes, external effects |
| `structural_signals` | mixed | Measured evidence from the codebase | LOC, import graph, fan-in/fan-out, complexity, MI, churn, coverage, SAST |
| `event_probabilities` | 0 to 1 each | Calibrated probabilities for adverse coding events | historical outcomes, action flags, blast radius, static analysis |
| `event_disutilities` | 0 to 1 each | Severity of each adverse event if it occurs | MCDA elicitation, project policy, user/stakeholder trade-offs |
| `testability` | 0 to 1 | How directly the action can be verified | targeted tests, coverage, deterministic reproduction |
| `evidence_quality` | 0 to 1 | Strength and relevance of evidence supporting the edit | repo-local facts, official docs, source/changelog evidence, calibration history |
| `source_reliability` | 0 to 1 | Trustworthiness of the evidence source | measured repo fact > official docs/source > general web result > model prior |
| `scope_control` | 0 to 1 | How narrow and bounded the proposed edit is | files touched, API surface, import/call fan-in, dependency/migration impact |
| `review_cost` | 0 to 1 | Human effort needed to review safely | diff size, files touched, conceptual complexity |
| `uncertainty` | variance / interval | How unreliable each estimate is | calibration error, model variance, bootstrap/conformal intervals |

Level 2 scores are derived:

| Level 2 Score | Range | Meaning | Formula Source |
|---|---:|---|---|
| `expected_loss` | 0 to 1+ | Expected disutility from adverse events | Sum of event probability times event disutility |
| `expected_utility` | unbounded raw utility | Expected value before uncertainty adjustment | Expected benefit minus expected loss and review cost |
| `risk_adjusted_utility` | unbounded raw utility | Conservative lower-bound utility | Mean utility minus confidence multiplier times utility SD |
| `edit_confidence` | 0 to 1 | Whether the agent has enough evidence to edit now | Weighted geometric mean or calibrated model over success, evidence quality, testability, reversibility, source reliability, and scope control |

Utility uses Level 2 `expected_loss`, not `blast_radius`, `criticality`, or `reversibility` directly. Those Level 1 scores remain visible because they explain the adverse-event probabilities and disutilities. `testability` affects `p_success`, uncertainty, and whether `test_first` is a cheap information-gathering recommendation.

### 7.4 Benefit

Benefit is not "the model sounds confident."

Benefit should estimate user value if the action works. The score should be elicited or learned with MCDA-style value functions, not guessed. Inputs:

- Directly resolves the stated task.
- Fixes a failing test or reproducible bug.
- Addresses high-priority files or user-blocking behavior.
- Avoids unnecessary broad changes.
- Matches explicit user constraints.

Example:

```text
0.90 - fixes the exact failing behavior and unblocks the user
0.60 - partial fix or likely progress
0.25 - exploratory refactor with unclear user value
```

For v1, PEBRA can start with a transparent MCDA value function:

```text
Benefit(a) = Σ_k w_k * v_k(a)
```

Where:

- `v_k(a)` is the action's score on criterion `k`.
- `w_k` is elicited by AHP, SMART/SMARTER, swing weighting, or DCE/conjoint.
- Weights are normalized so `Σ_k w_k = 1`.
- The selected elicitation method and consistency checks are stored in score provenance.

### 7.5 P(success)

`P(success)` should be calibrated over real outcomes. UQLM or CodeGenUQ scores are features, not final probabilities.

Feature examples:

- CodeGenUQ functional correctness confidence.
- UQLM consistency score.
- Number of candidate solutions that converge on same edit.
- Linter/typecheck expected pass.
- Diff size.
- Locality of edit.
- Historical agent success rate on similar actions.
- Test coverage of touched code.

Model:

```text
p_success = calibrated_model(features)
```

The calibration target is actual task outcome:

```text
success = issue resolved and no known regression introduced
```

Calibration is mandatory. A calibrated model should satisfy the practical interpretation that actions scored near `0.70` succeed about 70% of the time over comparable cases. Use reliability diagrams, Brier score decomposition, log loss, calibration slope/intercept, and stratified checks by repo/language/task type.

### 7.6 Evidence Aggregation

When several tools estimate the same quantity, PEBRA should combine them by precision rather than simple averaging.

If estimates are independent and have known or estimated variances:

```text
w_i = (1 / variance_i) / Σ_j (1 / variance_j)

pooled_estimate = Σ_i w_i * estimate_i
pooled_variance = 1 / Σ_i (1 / variance_i)
```

This is the inverse-variance weighting principle used in statistics and meta-analysis. It gives more weight to more precise evidence while preserving uncertainty.

If evidence sources are correlated, PEBRA should either model the covariance matrix or fall back to conservative aggregation, such as using the higher-risk estimate.

### 7.7 Blast Radius

PEBRA should consume blast-radius tools rather than rebuild them.

Recommended sources:

- `sem impact <entity> --json`
- `codeindex impact <file> --json`
- GitNexus `impact` MCP tool when licensing permits
- inspect triage output when licensing permits

Normalize raw impact:

```text
blast_radius =
  min(1.0,
      weighted_dependents / project_norm
      + public_api_penalty
      + cross_module_penalty
      + changed_entity_penalty)
```

For v1, use the tool's measured impact score with provenance. For v2, use weighted graph propagation:

```text
influence_ij = similarity_or_dependency_strength_ij ^ beta
```

The exponent `beta` should be selected by a graph-topology or validation criterion, borrowing the soft-threshold idea from WGCNA rather than choosing a constant by taste.

### 7.8 Criticality

Criticality comes from project policy, but the policy values should be elicited with MCDA or learned from outcomes. `.pebra.yml` stores the resulting value scale; it is not evidence that the values were invented arbitrarily.

```yaml
defaults:
  criticality: 0.50

criticality:
  "src/auth/**": 1.0
  "src/payments/**": 1.0
  "src/migrations/**": 0.95
  "src/ui/**": 0.45
  "tests/**": 0.20
  "docs/**": 0.10
```

For an action touching multiple files, aggregate criticality with a conservative rule by default:

```text
criticality(action) = max(criticality(file) for file in expected_files)
```

`max()` is deliberately conservative for risk scoring. A change touching both `src/auth/session.py` and `tests/test_auth.py` should inherit the auth risk, not average it away.

### 7.9 Adverse Event Model

PEBRA replaces hardcoded harm coefficients with an expected-loss model based on adverse-event probabilities and disutilities.

Adverse events are named categories. Their probabilities must be inferred from measured evidence, not from the label text itself.

Define adverse event classes:

| Event | Probability Features | Disutility Source |
|---|---|---|
| `test_regression` | blast radius, touched tests, missing coverage, historical regression rate | MCDA elicitation |
| `public_api_break` | exported symbol changed, symbol import fan-in, module import fan-in, dependency depth, dependent tests missing | MCDA elicitation |
| `migration_failure` | `requires_migration`, schema change, rollback plan, migration history | MCDA elicitation |
| `dependency_break` | dependency change, third-party import change, lockfile size, semver level, changelog/advisory signals | MCDA elicitation |
| `external_state_damage` | network use, DB writes, filesystem writes, external API writes | MCDA elicitation |
| `security_sensitive_change` | auth/payment path criticality, Bandit/SAST findings, secret/crypto/shell/SQL patterns | MCDA elicitation |
| `review_burden` | diff size, file count, monolith file, complexity, churn, conceptual spread | observed review outcomes or MCDA |

```text
p_event_j = calibrated_event_model_j(features)
disutility_j = MCDA_disutility_j

expected_loss(a) = Σ_j p_event_j(a) * disutility_j
```

This is the same core logic as expected loss in decision analysis: probability times severity, summed across adverse outcomes.

If there is not enough data to train event models, v1 should use transparent priors with provenance:

```text
p_event_j = prior_uncalibrated_j(action_class, repo_class, evidence_flags)
```

The prior must be versioned and later replaced or calibrated against observed outcomes. Do not label a probability source as calibrated until calibration has been checked against outcome data.

Example:

```text
Patch validate_login:

event                         p(event)   disutility   p * disutility
test_regression               0.10       0.40         0.04
public_api_break              0.03       0.80         0.02
security_sensitive_change     0.04       0.90         0.04
migration_failure             0.00       1.00         0.00
external_state_damage         0.00       1.00         0.00

expected_loss = 0.10
```

### 7.10 Review Cost

Review cost is a penalty for edits that humans or agents cannot easily audit.

Signals:

- Files touched.
- Lines changed.
- Public APIs changed.
- Tests changed without implementation changes.
- Multiple unrelated logical groups.
- Generated code or large lockfile changes.

Review cost should be learned from observed review burden when possible. Before enough local data exists, use an MCDA-derived review disutility or a calibrated prior by edit class.

### 7.11 Uncertainty

Uncertainty should increase when evidence is weak:

- No tests available.
- No dependency graph available.
- Candidate action is vague.
- Model outputs disagree.
- Touched language/framework is unsupported by available analyzers.
- Calibration data does not match this repo/language/task type.

PEBRA should store uncertainty as variance, confidence interval, or calibrated error estimate where possible. A single `uncertainty = 0.18` display value is acceptable only if the provenance explains how it was derived.

---

## 8. Decision Math

### 8.1 Expected Utility

The main v1 formula should be principle-based and auditable:

```text
expected_benefit = p_success * benefit
expected_loss    = Σ_j p_event_j * disutility_j

expected_utility =
  expected_benefit
  - expected_loss
  - expected_review_cost
```

This follows expected-utility / net-benefit logic. It replaces fixed coefficients with event probabilities and disutilities.

Risk adjustment should use a lower confidence bound:

```text
risk_adjusted_utility =
  E[utility] - z_alpha * SD(utility)
```

For v1, `SD(utility)` must be computed by a declared method, not guessed. The default method is first-order error propagation over the utility formula:

```text
U = p_success * benefit - Σ_j(p_event_j * disutility_j) - review_cost

Var(U) ≈
  benefit² * Var(p_success)
  + p_success² * Var(benefit)
  + Var(review_cost)
  + Σ_j[
      disutility_j² * Var(p_event_j)
      + p_event_j² * Var(disutility_j)
    ]
  + scenario_variance

SD(utility) = sqrt(Var(U))
```

This default approximation assumes independent inputs unless covariance terms are explicitly added. It can understate or overstate uncertainty when inputs are correlated, for example when high blast radius also increases review cost, raises adverse-event probability, and lowers `p_success`.

If estimates are strongly correlated or the implementation has distributions rather than scalar variances, PEBRA may use Monte Carlo sampling instead:

```text
sample p_success, benefit, p_event_j, disutility_j, review_cost
compute U for each sample
utility_sd = standard_deviation(U_samples)
```

Monte Carlo gates activate per metric/input only when PEBRA has defensible input distributions and correlation assumptions. That can happen before or after a named release version; the trigger is validated evidence from the outcome store or explicit project configuration, not the version label.

Monte Carlo RAU adds value only when PEBRA has defensible input distributions or correlation assumptions. Its main gain is not a different point estimate; it exposes decision uncertainty that first-order error propagation hides:

```text
E[utility]                 mean(U_samples)
utility_sd                 standard_deviation(U_samples)
RAU_alpha                  percentile(U_samples, alpha)
P(utility < 0)             fraction(U_samples < 0)
P(action is best)          fraction(action has max utility across paired samples)
```

Example output:

```text
Expected utility: 0.39
First-order RAU 90% lower bound: 0.31
Monte Carlo RAU 90% lower bound: 0.22
P(utility < 0): 0.14
P(action is best): 0.82
5th percentile utility: 0.18
```

This is useful for borderline edits, correlated risks, and cases where several candidate actions have overlapping utility intervals. Without real distributions, Monte Carlo is only simulation over guesses and should not be presented as stronger evidence than the inputs justify. The cost of Monte Carlo is modeling quality: choosing distributions, fitting or declaring covariance, and proving those assumptions are valid. The sampling computation itself is not the hard part.

The output must report the chosen method and a variance breakdown or sampling assumptions. Monte Carlo outputs must also carry provenance:

```text
distribution_source: fitted | configured | assumed
correlation_source: fitted | configured | independent_assumption | assumed
sample_count: integer
```

The distributions sampled here power the `P(utility < 0)` and `P(action is best)` gates in Sections 8.4 and 8.5. If distributions are not fitted or explicitly configured, PEBRA should fall back to first-order SD and interval-overlap heuristics.

```text
monte_carlo_gate_available =
  distribution_source in {"fitted", "configured"}
  and correlation_source in {"fitted", "configured", "independent_assumption"}
  and sample_count >= configured_min_sample_count
```

If `distribution_source` or `correlation_source` is only `assumed`, Monte Carlo results may be reported as exploratory diagnostics but must not drive hard gates.

`z_alpha` is selected from the desired one-sided confidence level, not hardcoded:

```text
90% conservative bound: z_alpha ≈ 1.28
95% conservative bound: z_alpha ≈ 1.64
```

Interpretation:

```text
RAU >  0.00   proceed candidate, subject to hard gates
RAU =  0.00   break-even under selected risk tolerance
RAU <  0.00   reject or ask human unless no safer alternative exists
```

Do not label raw utility as a percentage. If a UI needs a bounded display score, compute it separately and label it as a display transform.

### 8.2 Edit Confidence

RAU answers "is the action worth doing under risk?" Edit confidence answers "does the agent have enough evidence to perform this edit now?"

The v1 formula should stay simple, but should not multiply six factors directly. A raw product makes high confidence nearly unreachable. Use a weighted geometric mean so weak factors still pull the score down without collapsing the three-band design:

```text
edit_confidence =
  exp(Σ_i w_i * ln(x_i))

where:
  x_i ∈ {
    calibrated_p_success,
    evidence_quality,
    testability,
    reversibility,
    source_reliability,
    scope_control
  }
  Σ_i w_i = 1
```

All factors are normalized to `[0,1]` and must carry provenance. Equal weights are acceptable for v1 if the project has not configured policy weights. `scope_control` should decrease as file count, touched API surface, import fan-in, call fan-in, migration/dependency impact, and conceptual diff size increase. It should not be a raw line-count penalty alone, because some legitimate fixes require more than a few lines.

Default bands are configurable project policy, not universal truth:

```text
high confidence:
  make the smallest sufficient edit
  run normal verification

medium confidence:
  shrink scope
  inspect related files and repo-local patterns
  retrieve official docs or GitHub/source evidence if API, dependency, or framework behavior is uncertain
  run targeted verification
  auto-proceed only if confidence upgrades and residual risk is low

low confidence:
  avoid broad edit or refactor
  gather stronger evidence before editing
  present the evidence delta if confidence upgrades
  ask the user before proceeding unless project policy explicitly permits the transition
  propose a minimal patch or plan only
```

The controller rule is:

```text
When confidence drops, PEBRA should not let the agent guess harder.
It should gather better evidence, make a smaller edit, or ask for help.
```

This is confidence-gated retrieval-augmented editing. The agent's training data is treated as a prior. Medium- or low-confidence edits require observable evidence before code changes.

Confidence is mutable, but upgrades must be evidence-based. Repeating the model's own judgment is not enough to move from low to medium or high confidence.

```text
initial_assessment:
  confidence_band = low | medium | high

if confidence_band == high:
  proceed with smallest sufficient edit
  verify after edit

if confidence_band == medium:
  gather cheap local evidence
  run targeted tests or static checks
  re-score
  auto-proceed only if confidence improves and blast radius remains low
  ask_human if tests are missing, checks fail, or residual risk is material

if confidence_band == low:
  do not edit yet
  gather repo, docs, GitHub/source, or web evidence as needed
  re-score
  if confidence improves, present evidence_delta to the user
  proceed only with user approval or explicit project policy
```

The confidence transition rule is:

```text
confidence_upgrade_allowed only if:
  new evidence was gathered
  evidence source is reliable
  evidence matches the current repo, dependency version, or runtime
  the original uncertainty source was reduced
  remaining risks are stated
```

Retrieval alone should not produce perfect confidence. External evidence can be stale, version-mismatched, or misapplied, so projects should cap retrieval-only upgrades below 1.0.

These terms should not be treated as synonyms:

| Term | Role |
|---|---|
| `evidence_quality` | Relevance and completeness of the evidence for this exact edit |
| `source_reliability` | Authority of the source: measured repo fact, official docs/source, general web, or model prior |
| per-metric `confidence` | Trust in a specific metric estimate |
| `utility_sd` | Propagated numeric uncertainty in expected utility |
| `edit_confidence` | Controller score deciding whether the agent may edit now |

Weak evidence can lower `edit_confidence` and increase `utility_sd`; that precautionary double penalty is deliberate for autonomous edits. The implementation must still expose provenance so the same raw signal is not silently counted twice.

### 8.3 Evidence Escalation

PEBRA should use an evidence ladder instead of dumping web results into the prompt:

1. Local repo evidence: code, imports, tests, git history, call graph, dependency graph, project config.
2. Official documentation: framework, language, library, or API docs for the detected version.
3. GitHub/source evidence: upstream repository, changelog, release notes, issues, examples, advisories.
4. Web search: only when local, docs, and source evidence are insufficient.
5. User question: when the missing evidence is project intent, domain risk, or risk tolerance.

External evidence may inform behavior, API usage, edge cases, and failure modes. PEBRA must not copy external code verbatim into the local patch. The output should summarize extracted logic and cite provenance where available.

Retrieval has a cost: it can add latency and irrelevant context. PEBRA should inject summarized evidence patterns, not raw retrieved pages or copied code.

For low-confidence upgrades, PEBRA should return an evidence delta report:

```text
evidence_delta:
  missing_before: what made the first assessment low confidence
  gathered_now: repo/docs/GitHub/web/user evidence collected
  uncertainty_reduced: which uncertainty source changed
  confidence_change: old band/value -> new band/value
  remaining_risks: what is still uncertain
  proposed_next_step: proceed, test_first, ask_human, or reject
```

For medium-confidence upgrades, PEBRA should prefer test gates over user gates:

```text
medium + low residual risk + targeted checks pass:
  proceed with smallest sufficient edit

medium + material residual risk, missing tests, failed checks, or unclear intent:
  ask_human or inspect_first
```

### 8.4 Hard Gates

Some decisions should not depend on risk-adjusted utility:

```text
if action violates policy:
    reject

if expected_loss > LOSS_MAX:
    ask_human or reject

if monte_carlo_gate_available
and P(utility < 0) > MAX_P_NEGATIVE_UTILITY:
    ask_human or reject

if not monte_carlo_gate_available
and SD(utility) is high enough that RAU < 0 but E[utility] > 0:
    ask_human

if monte_carlo_gate_available
and decision_instability > DECISION_INSTABILITY_THRESHOLD:
    inspect_first or test_first

if not monte_carlo_gate_available
and cheap_information_available and uncertainty could change the decision:
    inspect_first or test_first

if edit_confidence is below LOW_CONFIDENCE_THRESHOLD:
    gather_evidence, ask_human, or propose_minimal_plan_only

if confidence_upgrade_requested and no new evidence_delta exists:
    reject_upgrade

if low_confidence_upgraded and user_confirmation_required:
    ask_human_before_edit
```

### 8.5 Information Gathering

Information-gathering actions are different from edit actions.

Examples:

- Inspect the failing test.
- Run a targeted test.
- Search for call sites.
- Ask the user to choose between two interpretations.

PEBRA should use the best uncertainty gate justified by available evidence. This is not full EVPI; it is a practical ranking-fragility check.

```text
if monte_carlo_gate_available:
  decision_instability = 1 - P(top_action_is_best)

if not monte_carlo_gate_available:
  decision_instability = interval_overlap_heuristic(top_action, second_action)
```

With fitted or configured distributions, paired Monte Carlo samples compute `P(top_action_is_best)` directly:

```text
for each sample:
  compute utility for each candidate action
  record which action has max utility

P(action_i is best) = count(action_i wins) / sample_count
decision_instability = 1 - P(current_top_action is best)
```

Without justified distributions, approximate instability with calibration intervals and scenario analysis:

```text
if top_action_RAU_interval overlaps second_action_RAU_interval
and cheap information can reduce the largest uncertainty source:
    inspect_first or test_first
```

Monte Carlo replaces the interval-overlap heuristic when its distribution provenance is `fitted` or explicitly `configured`; it should not run alongside the heuristic as a competing definition. EVPI, EVPPI, and CEAC belong in the v2/research appendix until the required risk-tolerance semantics are specified.

---

## 9. Decision Output

### 9.1 Human-Readable Table

This is an abbreviated view of the canonical JSON in Section 9.2.

```text
PEBRA assessment

Task: Fix failing login validation

| Action               | Benefit | P(success) | Expected Loss | RAU   | Confidence | Decision |
|----------------------|---------|------------|---------------|-------|------------|----------|
| Inspect auth tests   | --      | --         | 0.02          | --    | --         | Completed |
| Patch validate_login | 0.82    | 0.74       | 0.10          | 0.31  | High after evidence | Proceed with confirmation |
| Refactor auth module | 0.88    | 0.41       | 0.58          | -0.70 | Low        | Reject   |
| Upgrade auth dependency | 0.63 | 0.48       | 0.49          | -0.54 | Low        | Ask human |

Recommended next step: Present the evidence delta, then patch `validate_login` if the user confirms.

Rationale:
- Initial uncertainty was reduced by inspecting auth tests and local call sites.
- Broad auth refactor has high expected loss and weak conservative utility.
- Dependency upgrade has weak utility and broad unknowns.
- Targeted patch is now the best edit action, with a small-edit policy and user confirmation because it upgraded from low confidence.
```

### 9.2 Canonical JSON Output

Every metric is an object, not a bare number. This prevents precision theater by telling the consuming agent whether a score was measured, configured, estimated, or derived.

Worked examples are part of the spec. Numeric example values must be computed from the stated formulas and factor values, not manually invented. A future docs check should parse the examples and fail if derived values drift from their formulas.

Allowed `source` values:

| Source | Meaning |
|---|---|
| `measured` | Computed from a tool or repo fact, such as sem output or action flags |
| `configured` | Read from project policy such as `.pebra.yml` |
| `elicited` | Produced by MCDA, AHP, SMART/SMARTER, swing weighting, or DCE/conjoint |
| `estimated` | Inferred from model features, heuristics, or historical calibration |
| `derived` | Calculated from other metric values by a declared formula |
| `prior_uncalibrated` | Transparent startup prior used before enough local outcome data exists for calibration |

```json
{
  "task": "Fix failing login validation",
  "recommended_decision": "proceed_with_confirmation",
  "recommended_action_id": "a1",
  "actions": [
    {
      "id": "info_1",
      "label": "Inspect auth tests and local call sites",
      "action_type": "information_gathering",
      "scores": {
        "expected_loss": {
          "value": 0.02,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.80,
          "formula": "inspection_cost + delay_cost",
          "evidence": ["Read-only local inspection with no code or external-state changes."]
        }
      },
      "decision": "completed",
      "evidence": [
        "Targeted auth test path was found.",
        "Local call-site search found limited dependent usage."
      ]
    },
    {
      "id": "a1",
      "label": "Patch validate_login only",
      "edit_control": {
        "initial_confidence_band": "low",
        "edit_confidence": {
          "value": 0.83,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.60,
          "formula": "exp(mean(ln([p_success, evidence_quality, testability, reversibility, source_reliability, scope_control])))",
          "evidence": [
            "Targeted test exists.",
            "Relevant code is localized.",
            "No schema, dependency, migration, or external-state write detected."
          ],
          "factors": {
            "evidence_quality": {
              "value": 0.78,
              "source": "measured",
              "confidence": 0.72,
              "evidence": ["Repo-local tests and related code were inspected."]
            },
            "source_reliability": {
              "value": 0.86,
              "source": "estimated",
              "confidence": 0.75,
              "method": "source-authority rubric",
              "evidence": ["Primary evidence comes from repo facts and targeted tests, not general web snippets."]
            },
            "scope_control": {
              "value": 0.92,
              "source": "measured",
              "confidence": 0.82,
              "evidence": ["One production file and one targeted test file expected; no public API, dependency, or migration change."]
            }
          }
        },
        "confidence_band": "high",
        "confidence_transition": {
          "from": "low",
          "to": "high",
          "upgrade_allowed": true,
          "requires_user_confirmation": true,
          "reason": "Repo-local evidence reduced uncertainty about call sites and testability.",
          "evidence_delta": {
            "missing_before": [
              "Call sites for validate_login were not inspected.",
              "Targeted test coverage was unknown."
            ],
            "gathered_now": [
              "Local call-site search found limited usage.",
              "Targeted auth test exists and reproduces the relevant behavior."
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
        "required_evidence_action": "present evidence delta and request confirmation because confidence upgraded from low",
        "edit_policy": "smallest_sufficient_edit; no broad refactor"
      },
      "scores": {
        "benefit": {
          "value": 0.82,
          "level": "level_1",
          "source": "elicited",
          "confidence": 0.70,
          "evidence": ["Directly addresses the failing login-validation task."],
          "method": "MCDA value function with normalized criterion weights"
        },
        "p_success": {
          "value": 0.74,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.62,
          "evidence": ["Localized action with targeted test plan and no dependency change."]
        },
        "blast_radius": {
          "value": 0.18,
          "level": "level_1",
          "source": "measured",
          "confidence": 0.84,
          "evidence": ["sem impact found low transitive dependency count for src/auth.py."]
        },
        "structural_signals": {
          "value": [
            {
              "signal": "file_loc",
              "value": 240,
              "risk_band": "low",
              "normalization": "absolute_and_repo_percentile",
              "percentile": 0.42,
              "source": "radon",
              "confidence": 0.95
            },
            {
              "signal": "module_import_fan_in",
              "value": 8,
              "risk_band": "moderate",
              "normalization": "repo_percentile",
              "percentile": 0.68,
              "source": "ast_import_graph",
              "confidence": 0.86
            },
            {
              "signal": "symbol_import_fan_in",
              "value": 5,
              "risk_band": "moderate",
              "normalization": "repo_percentile",
              "percentile": 0.71,
              "source": "ast_import_graph",
              "confidence": 0.80
            },
            {
              "signal": "circular_import_touched",
              "value": false,
              "risk_band": "low",
              "normalization": "boolean",
              "source": "import_graph_scc",
              "confidence": 0.86
            },
            {
              "signal": "call_graph_fan_in",
              "value": 14,
              "risk_band": "high",
              "normalization": "repo_percentile",
              "percentile": 0.93,
              "source": "sem",
              "confidence": 0.84
            },
            {
              "signal": "cyclomatic_complexity",
              "value": 6,
              "risk_band": "low",
              "normalization": "mccabe_threshold",
              "source": "radon",
              "confidence": 0.92
            },
            {
              "signal": "security_static_analysis",
              "value": {
                "high": 0,
                "medium": 0,
                "low": 0
              },
              "risk_band": "low",
              "source": "bandit",
              "confidence": 0.88
            }
          ],
          "level": "level_1",
          "source": "measured",
          "confidence": 0.84,
          "evidence": ["Structural signals were collected locally before event probability estimation."]
        },
        "criticality": {
          "value": 1.0,
          "level": "level_1",
          "source": "configured",
          "confidence": 0.95,
          "evidence": [".pebra.yml maps src/auth/** to criticality 1.0."],
          "method": "Stored project policy value; policy values should be elicited with MCDA or learned from outcomes."
        },
        "reversibility": {
          "value": 0.92,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.80,
          "evidence": ["No schema, migration, dependency, network, or external-state change required."]
        },
        "action_flags": {
          "value": {
            "requires_dependency_change": false,
            "requires_schema_change": false,
            "requires_network": false,
            "requires_migration": false,
            "writes_external_state": false
          },
          "level": "level_1",
          "source": "measured",
          "confidence": 0.90,
          "evidence": ["All side-effect flags are false."],
          "note": "Runtime loss is computed from adverse-event probabilities and disutilities, not from fixed side-effect weights."
        },
        "testability": {
          "value": 0.80,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.78,
          "evidence": ["tests/test_auth.py is a targeted verification path."]
        },
        "review_cost": {
          "value": 0.12,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.76,
          "evidence": ["Expected diff is localized to one implementation file and one test file."]
        },
        "utility_sd": {
          "value": 0.06,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.65,
          "method": "first_order_error_propagation",
          "formula": "sqrt(benefit^2*Var(p_success) + p_success^2*Var(benefit) + Var(review_cost) + sum(disutility_j^2*Var(p_event_j) + p_event_j^2*Var(disutility_j)) + scenario_variance)",
          "variance_breakdown": {
            "p_success": 0.0016,
            "benefit": 0.0004,
            "event_losses": 0.0009,
            "review_cost": 0.0004,
            "scenario_variance": 0.0003,
            "total_variance": 0.0036
          },
          "evidence": ["sqrt(0.0036) = 0.06 from the stated variance breakdown."]
        },
        "event_losses": {
          "value": [
            {
              "event": "test_regression",
              "p_event": 0.10,
              "disutility": 0.40,
              "expected_loss": 0.04,
              "probability_source": "prior_uncalibrated",
              "disutility_source": "mcda_elicited"
            },
            {
              "event": "public_api_break",
              "p_event": 0.03,
              "disutility": 0.80,
              "expected_loss": 0.02,
              "probability_source": "sem_features",
              "disutility_source": "mcda_elicited"
            },
            {
              "event": "security_sensitive_change",
              "p_event": 0.04,
              "disutility": 0.90,
              "expected_loss": 0.04,
              "probability_source": "path_criticality_prior",
              "disutility_source": "mcda_elicited"
            }
          ],
          "level": "level_1",
          "source": "prior_uncalibrated",
          "confidence": 0.62,
          "evidence": ["Adverse-event model uses transparent startup priors until enough local outcomes exist for calibration."]
        },
        "expected_loss": {
          "value": 0.10,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.62,
          "formula": "sum(p_event_j * disutility_j)"
        },
        "expected_utility": {
          "value": 0.39,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.60,
          "formula": "p_success * benefit - expected_loss - review_cost"
        },
        "risk_adjusted_utility": {
          "value": 0.31,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.60,
          "formula": "expected_utility - z_alpha * utility_sd",
          "parameters": {
            "confidence_level": 0.90,
            "z_alpha": 1.28
          }
        }
      },
      "decision": "proceed",
      "evidence": [
        "Expected files are localized to src/auth.py and tests/test_auth.py.",
        "Targeted test exists.",
        "No schema, dependency, or migration change required."
      ]
    },
    {
      "id": "a2",
      "label": "Refactor auth module",
      "action_type": "edit",
      "scores": {
        "benefit": {
          "value": 0.88,
          "level": "level_1",
          "source": "elicited",
          "confidence": 0.55,
          "evidence": ["Could improve auth structure, but exceeds the immediate validation task."]
        },
        "p_success": {
          "value": 0.41,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.50,
          "evidence": ["Broad refactor with high call-site exposure and no need for dependency or schema change."]
        },
        "expected_loss": {
          "value": 0.58,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.55,
          "formula": "sum(p_event_j * disutility_j)"
        },
        "risk_adjusted_utility": {
          "value": -0.70,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.50,
          "formula": "expected_utility - z_alpha * utility_sd"
        }
      },
      "edit_control": {
        "confidence_band": "low",
        "edit_policy": "reject broad refactor; propose narrower patch"
      },
      "decision": "reject",
      "evidence": ["Expected loss and review burden are too high for the stated task."]
    },
    {
      "id": "a3",
      "label": "Upgrade auth dependency",
      "action_type": "edit",
      "scores": {
        "benefit": {
          "value": 0.63,
          "level": "level_1",
          "source": "elicited",
          "confidence": 0.50,
          "evidence": ["May fix the behavior if the bug is dependency-related, but no local evidence currently supports that cause."]
        },
        "p_success": {
          "value": 0.48,
          "level": "level_1",
          "source": "estimated",
          "confidence": 0.45,
          "evidence": ["Dependency behavior is uncertain and would require release-note/advisory evidence."]
        },
        "expected_loss": {
          "value": 0.49,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.50,
          "formula": "sum(p_event_j * disutility_j)"
        },
        "risk_adjusted_utility": {
          "value": -0.54,
          "level": "level_2",
          "source": "derived",
          "confidence": 0.45,
          "formula": "expected_utility - z_alpha * utility_sd"
        }
      },
      "edit_control": {
        "confidence_band": "low",
        "required_evidence_action": "check official release notes, changelog, advisories, and lockfile diff before reconsidering",
        "edit_policy": "ask human; do not upgrade dependency as first-line fix"
      },
      "decision": "ask_human",
      "evidence": ["Dependency upgrade has broad unknowns and weak task-specific evidence."]
    }
  ]
}
```

---

## 10. Tool Architecture

### 10.1 Components

```text
Agent / CLI
   |
   v
PEBRA MCP server
   |
   +-- Candidate action parser
   +-- Evidence collector
   |     +-- git diff/status
   |     +-- structural metrics: LOC, complexity, maintainability
   |     +-- import graph signals: module imports, symbol imports, cycles, dynamic imports
   |     +-- call graph and dependency graph signals
   |     +-- git history / churn / hotspot signals
   |     +-- security static-analysis signals
   |     +-- sem/codeindex/GitNexus/inspect
   |     +-- test discovery
   |     +-- repo config
   |
   +-- Score normalizer
   +-- Confidence gate
   +-- Decision engine
   +-- Explanation generator
   +-- Outcome logger
   +-- Calibration store
```

### 10.2 Local Config

Each repo can define risk policy in `.pebra.yml`:

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

### 10.3 Outcome Store

PEBRA improves only if it records whether it was right.

Store:

- Candidate actions.
- Scores and evidence.
- Agent decision.
- Files actually changed.
- Tests run.
- Test results.
- Human review result.
- Whether the task was resolved.
- Whether regressions were found later.

This supports calibration:

```text
When PEBRA says p_success = 0.70, do roughly 70% of those actions succeed?
```

---

## 11. Existing Tools and Licensing Notes

### 11.1 Blast Radius and Code-Risk Tools

| Tool | Best Use | License / Constraint | PEBRA Recommendation |
|---|---|---|---|
| sem | Entity-level diff, blame, and impact analysis on Git | MIT OR Apache-2.0 | Primary v1 blast-radius input |
| codeindex | Per-file blast-radius scoring, CLI, MCP, temporal graph | Apache 2.0 | Good fallback or companion |
| GitNexus | MCP-native impact analysis and code graph | PolyForm Noncommercial; commercial license needed | Optional, not default for commercial v1 |
| inspect | Entity-level PR triage, risk scoring, MCP tools | FSL-1.1-ALv2 Future License; competing use restricted until future Apache grant | Useful reference; be careful as dependency |
| TDAD | Code-test dependency graph and affected-test mapping | Verify implementation license before use | Useful for testability and affected tests |

### 11.2 Structural Evidence Tools

| Tool | Best Use | License / Constraint | PEBRA Recommendation |
|---|---|---|---|
| radon | Python LOC, raw metrics, McCabe complexity, Halstead, Maintainability Index | MIT | Primary Python structural metrics adapter |
| Bandit | Python AST-based security-sensitive operation detection | Apache-2.0 | Primary Python security evidence adapter |
| lizard | Multi-language cyclomatic complexity and static-analysis metrics | MIT | Multi-language complexity fallback |
| ast-metrics | Multi-language architecture, complexity, coupling, maintainability, bus factor, reports, MCP | MIT | Strong candidate for broad structural evidence |
| grimp / import-linter | Python import graph and architecture boundary rules | Verify current license before use | Useful Python import-boundary adapter |
| pydeps / modulegraph | Python module dependency/import visualization and analysis | Verify current license before use | Optional Python import graph adapter |
| madge | JavaScript/TypeScript dependency graph and circular dependency detection | Verify current license before use | Optional JS/TS import graph adapter |
| dependency-cruiser | JavaScript/TypeScript dependency graph and architecture rules | Verify current license before use | Optional JS/TS import-boundary adapter |
| module-coupling-metrics | Python fan-in, fan-out, instability definitions | AGPL-3 | Do not ship as default production dependency; borrow definitions only or isolate if license is acceptable |

### 11.3 Uncertainty and Calibration Tools

| Tool | Use | License |
|---|---|---|
| UQLM / CodeGenUQ | Code-generation uncertainty features | Apache 2.0 |
| MAPIE | Conformal intervals and risk control | BSD-3 |
| Uncertainty Toolbox | Calibration metrics and plots | MIT |
| properscoring | CRPS and proper scoring rules | Apache 2.0 |
| scikit-learn | Calibration models, Brier score | BSD-3 |

### 11.4 MCDA Tools

MCDA is part of v1 because benefit and disutility weights must be elicited or justified. A first implementation can use a small MCDA value function before adding broader ranking methods.

| Tool | Use | License |
|---|---|---|
| AHPy | Pairwise weight elicitation | MIT |
| pyrepo-mcda | TOPSIS and MCDA methods | MIT |
| pymcdm | TOPSIS/VIKOR alternatives | MIT |
| scikit-criteria | MCDA framework | BSD-3 |
| pysensmcda | Ranking sensitivity | MIT |

### 11.5 GPL / Copyleft Avoidance

Do not ship GPL, AGPL, or other strong-copyleft packages as runtime dependencies unless the project intentionally accepts those obligations.

If v2 adds heavier decision-analysis methods, implement them from public formulas rather than GPL runtime dependencies:

- ICER
- NMB
- EVPI
- CEAC
- PSA

GPL tools can be used only as offline validation references with clean-room separation.

---

## 12. MVP Scope

### 12.1 v1 Should Include

- MCP server with `pebra_compare`.
- CLI with `pebra assess`.
- JSON input/output.
- Tier-1 Evidence Discovery Layer with measured Python/local signals:
  - LOC and complexity via `radon`.
  - Python AST import graph fan-in/fan-out.
  - Python security static analysis via `bandit`.
  - Blast radius through `sem` when available.
  - Git diff/status and targeted test discovery.
- Principle-based score normalization.
- MCDA-derived benefit and disutility scales.
- Expected-loss event model.
- Risk-adjusted utility scoring.
- Repo-level `.pebra.yml`.
- Human-readable decision table.
- Outcome logging schema.
- Basic calibration report.

### 12.1.1 v1.5 Should Add

- Multi-language import and dependency adapters.
- Call graph adapters beyond the first supported language.
- Maintainability Index and coverage mapping where tools are available.
- Monte Carlo decision gates automatically activate when validated input distributions or calibrated outcome data exist.
- Broader `codeindex`, GitNexus, inspect, and vendor-specific integrations after the core loop is stable.

### 12.2 v1 Should Not Include

- A full new code graph engine.
- Full multi-language evidence discovery.
- Complex pharmacoeconomic modeling beyond risk-adjusted utility.
- Runtime EVPI, EVPPI, CEAC, or PSA.
- Automatic edits.
- Claims of universal correctness.
- Overprecise decimal scores without evidence.
- Risk labels used as direct model inputs without measured evidence.
- Broad vendor-specific integrations before the core loop works.

### 12.3 Success Criteria

PEBRA v1 is useful if:

- It makes agents choose narrower edits when broad edits have poor expected value.
- It discovers structural risk from measurable repo signals instead of plain-text risk labels.
- It recommends tests/inspection when uncertainty could change the decision and information is cheap.
- Its `P(success)` scores calibrate over time.
- It produces rationales humans can audit.
- It integrates with agents through MCP without changing the agent runtime.

---

## 13. Evaluation Plan

### 13.1 Best Evaluation Sources

Use multiple datasets:

| Dataset | Role |
|---|---|
| SWE-bench Verified | Pilot and historical baseline only |
| SWE-bench Pro | Better frontier benchmark target when available |
| Private repo task logs | Best real-world calibration source |
| Generated candidate patches | Required because benchmarks usually provide tasks, not action choices |

SWE-bench Verified should not be the only main proof target because it has known contamination and test-quality concerns. It is still useful for a pilot and for comparing against older work.

### 13.2 Evaluation Protocol

For each task:

1. Generate 3 to 6 candidate actions or patches using the same base agent.
2. Run PEBRA before revealing final test outcomes.
3. Let PEBRA rank actions or request information.
4. Execute the selected action.
5. Record whether the issue was resolved.
6. Record regressions, review burden, files touched, and test results.

### 13.3 Baselines

Compare PEBRA against:

- Agent default choice.
- Smallest diff.
- Lowest blast radius.
- Highest CodeGenUQ/UQLM confidence.
- Highest testability.
- Random candidate.

### 13.4 Metrics

| Metric | Question |
|---|---|
| Selection accuracy | Did PEBRA choose the best candidate action? |
| Regression avoidance | Did PEBRA avoid high expected-loss failed edits? |
| Structural signal validity | Do LOC, import graph, fan-in, complexity, churn, SAST, and coverage signals improve event prediction? |
| Brier score | Is `P(success)` calibrated? |
| Calibration slope/intercept | Is the model systematically overconfident? |
| Human escalation precision | Were escalations actually useful? |
| Information-gathering precision | Did `inspect_first` or `test_first` reduce failure? |
| Review cost reduction | Did PEBRA avoid noisy broad diffs? |
| Monte Carlo decision value | When enabled, did `P(utility < 0)` or `P(action is best)` improve borderline decisions? |

---

## 14. Implementation Quickstart

Recommended v1 stack:

```bash
# Blast radius source
cargo install sem-cli

# Optional fallback blast radius source
pip install codeindex

# Structural evidence adapters
pip install radon bandit lizard

# Core math and calibration
pip install numpy scipy pandas scikit-learn
pip install uqlm mapie uncertainty-toolbox properscoring
```

`ast-metrics` is distributed as a standalone binary and can be added as a structural evidence adapter when broad architecture, coupling, maintainability, bus factor, or MCP-aware reports are needed.

Example CLI:

```bash
pebra assess \
  --task "Fix failing login validation" \
  --actions actions.json \
  --repo .
```

Example output:

```text
Decision: inspect_first

Why:
- The targeted patch is promising, but uncertainty is still meaningful.
- Running tests/test_auth.py is cheap and highly informative.
- Broad auth refactor has high expected loss and weak risk-adjusted utility.
```

---

## 15. Updated Positioning

Old framing:

> PEBRA is a paper about a pharmacoeconomic framework for coding agents.

Better framing:

> PEBRA is a pre-edit decision engine for coding agents. It scores candidate actions with principle-based Level 1 evidence, derives expected loss and risk-adjusted utility, then recommends whether to proceed, inspect, test, ask a human, or reject.

Short pitch:

> CI checks code after it exists. PEBRA checks whether an agent should create that code in the first place.

---

## 16. Open Design Questions

1. Should v1 be MCP-first, CLI-first, or both?
2. Should PEBRA generate candidate actions itself, or only score actions proposed by the agent?
3. Should benefit be inferred automatically, configured by the user, or both?
4. Should project risk tolerance be a single number or per-directory policy?
5. Should outcome logging be local-only by default?
6. Which first agent should PEBRA target: Codex, Claude Code, Cursor, or any MCP client?

---

## 17. V2 / Research Appendix

The following ideas are intentionally out of the v1 runtime path:

- EVPI and EVPPI for formal value-of-information analysis.
- CEAC curves over risk tolerance.
- Full PSA over risk tolerance and model structure.
- ICER and NMB comparisons beyond the v1 expected-utility score.

Monte Carlo sampling for `P(utility < 0)` and `P(action is best)` is allowed earlier when distribution provenance is fitted or explicitly configured. Full PSA and CEAC remain deferred because they need broader risk-sample definitions and risk-tolerance semantics before they belong in runtime scoring.

---

## 18. Methods References

PEBRA should cite and implement from these method families:

| Method Family | PEBRA Use | Reference Anchor |
|---|---|---|
| ISPOR MCDA good-practice guidance | Structured criteria, scoring, weight elicitation, sensitivity analysis | https://www.ispor.org/docs/default-source/publications/value-outcomes-spotlight/march-april-2016/valueandoutcomesspotlight_mcda_tfr2-summary.pdf |
| Net benefit / health economic evaluation | Expected utility and risk-tolerance framing | https://www.treeage.com/help/Content/13-Cost-Effectiveness-Analysis/4-Net-Benefits-Calculations.htm |
| ISPOR-SMDM uncertainty guidance | Probabilistic uncertainty, EVPI, CEAC, reporting calibrated parameter uncertainty | https://www.ispor.org/docs/default-source/resources/outcomes-research-guidelines-index/model_parameter_estimation_and_uncertainty-6.pdf |
| Inverse-variance weighting | Precision-weighted evidence aggregation | https://www.nist.gov/document/combine-1pdf |
| WGCNA | Weighted graph propagation and soft adjacency over dependency graphs | https://link.springer.com/article/10.1186/1471-2105-9-559 |
| Probability calibration | Calibrated `p_success` and event probabilities | https://scikit-learn.org/stable/modules/calibration.html |
| Learning to rank | Later learned ranking from agent outcomes | https://xgboost.readthedocs.io/en/latest/tutorials/learning_to_rank.html |
| Radon | Python LOC, cyclomatic complexity, Halstead, Maintainability Index | https://github.com/rubik/radon |
| Bandit | Python AST-based security issue detection | https://github.com/PyCQA/bandit |
| AST Metrics | Architecture, coupling, complexity, maintainability, bus factor, MCP reports | https://github.com/Halleck45/ast-metrics |
| Lizard | Multi-language cyclomatic complexity analyzer | https://github.com/terryyin/lizard |
| McCabe complexity thresholds | Complexity risk bands for testability/change risk | https://support.scitools.com/support/solutions/articles/70000582297-understanding-mccabe-cyclomatic-complexity |

---

## 19. Recommended Next Step

Build the smallest useful version:

1. `pebra_compare` MCP tool.
2. `.pebra.yml` risk policy.
3. Evidence Discovery Layer with import graph analysis, `sem`, `radon`, `bandit`, and optional `lizard`/`ast-metrics` adapters.
4. Principle-based risk-adjusted utility scoring.
5. Decision table output.
6. Outcome logging for calibration.

Do not start with a large ML model. Start with an auditable scoring engine that agents can call before editing. Then collect outcomes and calibrate the scoring model from real use.
