# Bounded Impact Witnesses and Assay Wiring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add deterministic, bounded dependency witnesses to PEBRA's explanations and make the agent A/B assay test that exact shipped advisory without changing decision math or efficacy endpoints.

**Architecture:** `CodeGraphAdapter` will retain a small structured witness beside each owner's existing complete impact-ID set. The structured witness flows through `symbol_fanin`, explanation generation, JSON/model guidance, and the CLI, while decision and scoring code continue to consume only existing fields. The experiment projects the same structured witness into its existing arm-neutral `advisory` string, authenticates the changed treatment in preflight and the design hash, and records exposure as host-only process telemetry.

**Tech Stack:** Python 3.11+, SQLite/CodeGraph read adapter, dataclasses, existing PEBRA composition/model-guidance pipeline, pytest, nox, import-linter.

---

## Scope and Locked Invariants

- Feature boundary: cite bounded changed-owner-to-dependent evidence only. Do not reconstruct complete transitive paths, add a safety-case export, or change risk math.
- Keep `impacted_node_ids` complete because candidate aggregation uses that set for counts and deduplication. The new witness list is a separate bounded explanatory projection.
- Prefer direct depth-1 edge locations when `edges.line`/`edges.col` are usable. Otherwise cite the dependent node's definition location.
- Select witnesses deterministically by `(depth, file_path, qualified_name, node_id)`.
- Cap retained witnesses at three per changed owner and cap serialized/explained witnesses at five per candidate.
- Never use `edges.provenance`; the current live graph has it unset. A witness is grounded by source node, target owner/target, edge kind, depth, and location.
- Do not add predecessor tracking or recursive path enumeration. Depth greater than one is reported honestly as transitive reach with the dependent definition location.
- Do not change `expected_loss`, utility, RAU, gates, recommended decision, confirmation state, or candidate aggregation.
- Experiment output remains exactly `{recommended_decision, risk_level, advisory, detail}` and `detail` retains its arm-neutral shape.
- Model-facing experiment text may include repository paths, symbol names, line/column, edge kind, and depth. It must not include PEBRA, CodeGraph, graph/fan-in/blast vocabulary, node IDs, provider/index versions, graph-scope digests, hidden task labels, or arm names.
- Witness presence and model repetition are not efficacy outcomes. Existing harm, completion, over-caution, and adherence endpoints remain unchanged.
- Existing pre-witness runs remain valid for their old treatment but must not be resumed or pooled with witness-enabled runs.
- This plan enriches the current multi-arm assay treatment. It does not implement or claim the separately proposed two-arm v5 assay redesign.
- No paid/provider-backed experiment, release, tag, or publish occurs under this plan.

## Milestone 0: Isolate Work and Establish Baseline

**Files:**

- No source changes.

**Step 1: Preserve current user work**

Run:

```powershell
git status --short --branch
git diff -- e2e/experiments/agent_ab/README.md e2e/utils/cli_harness.py
```

Expected:

- The current uncommitted experiment gate-contract work is visible.
- Nothing is reset, stashed, reformatted, or overwritten.

**Step 2: Create an isolated implementation worktree**

Run from the main checkout after the current changes are safely committed by their owner:

```powershell
git worktree add ..\pebra-impact-witnesses -b codex/bounded-impact-witnesses
```

Expected:

- Implementation occurs on `codex/bounded-impact-witnesses`.
- The original dirty checkout remains untouched.

**Step 3: Carry the ignored plan into the implementation branch**

The repository intentionally ignores `docs/`, so a new worktree will not contain this plan. Copy and
force-track this one file without changing the broad ignore rule:

```powershell
New-Item -ItemType Directory -Force ..\pebra-impact-witnesses\docs\plans | Out-Null
Copy-Item .\docs\plans\2026-07-25-bounded-impact-witnesses-and-assay-wiring.md ..\pebra-impact-witnesses\docs\plans\2026-07-25-bounded-impact-witnesses-and-assay-wiring.md
Set-Location ..\pebra-impact-witnesses
git add -f docs/plans/2026-07-25-bounded-impact-witnesses-and-assay-wiring.md
git commit -m "docs: plan bounded impact witnesses"
```

Expected:

- The implementation branch contains the reviewed plan.
- `.gitignore` remains unchanged; other ignored documents are not exposed.

**Step 4: Run the focused baseline**

Run:

```powershell
pytest tests/unit/test_codegraph_adapter.py tests/unit/test_candidate_aggregation.py tests/unit/test_assessment_builder.py tests/unit/test_explanation_generator.py e2e/experiments/agent_ab/tests/test_advisory_shape.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/experiments/agent_ab/tests/test_assay_design.py -q
```

Expected: PASS.

## Milestone 1: Retain Structured Witnesses Without Changing Impact Counts

**Files:**

- Modify: `pebra/core/models.py:93`
- Modify: `pebra/adapters/codegraph_adapter.py:1342`
- Modify: `tests/unit/test_codegraph_adapter.py:66`

**Step 1: Extend the test edge helper**

Change `_edge` so tests can explicitly seed edge line and column while existing callers retain their current behavior:

```python
def _edge(
    con,
    src,
    tgt,
    kind="calls",
    provenance="tree-sitter",
    *,
    line=None,
    col=None,
):
    con.execute(
        "INSERT INTO edges (source, target, kind, line, col, provenance) VALUES (?,?,?,?,?,?)",
        (src, tgt, kind, line, col, provenance),
    )
```

**Step 2: Write failing adapter tests**

Add tests proving:

1. A direct dependent with an edge line produces an `edge_site` witness.
2. A direct dependent with no edge line produces a `node_definition` witness.
3. A depth-2 dependent reports `depth == 2` and uses its definition location without claiming a complete path.
4. More than three reached nodes preserve every `impacted_node_id` but retain exactly three deterministic witnesses.
5. Reversing insertion order produces identical witness tuples.
6. Changed owners remain excluded from each other's witness set.
7. The depth-1 edge lookup joins against `_modify_impact_target_ids(...)`, not raw owner IDs.

Example assertion:

```python
owner = next(item for item in evidence.owner_risk if item.node_id == "func:A")
assert owner.impacted_node_ids == ("caller:1", "caller:2", "caller:3", "caller:4")
assert owner.impact_witnesses == (
    ImpactWitness(
        impacted_node_id="caller:1",
        qualified_name="pkg.caller_one",
        file_path="src/caller.py",
        line=42,
        column=7,
        edge_kind="calls",
        depth=1,
        location_source="edge_site",
    ),
    # two more deterministic witnesses
)
```

**Step 3: Run the new tests and verify failure**

Run:

```powershell
pytest tests/unit/test_codegraph_adapter.py -k "impact_witness" -q
```

Expected: FAIL because `ImpactWitness` and `OwnerRiskEvidence.impact_witnesses` do not exist.

**Step 4: Add the immutable witness model**

Add beside `OwnerRiskEvidence`:

```python
@dataclass(frozen=True)
class ImpactWitness:
    impacted_node_id: str
    qualified_name: str = ""
    file_path: str = ""
    line: int | None = None
    column: int | None = None
    edge_kind: str = ""
    depth: int = 1
    location_source: str = "node_definition"
```

Add to `OwnerRiskEvidence`:

```python
impact_witnesses: tuple[ImpactWitness, ...] = ()
```

The default preserves every existing constructor and fixture.

**Step 5: Implement bounded witness lookup**

In `CodeGraphAdapter`:

- Add `_MAX_IMPACT_WITNESSES_PER_OWNER = 3`.
- Keep `_transitive_impact_nodes()` unchanged.
- Add a private helper that receives the current owner's `targets` and the already-fetched `reached` rows.
- Sort reached rows by depth, then join selected node IDs to `nodes` for qualified name, file, and definition line/column.
- For depth 1 only, query `edges` with:

```sql
WHERE source IN (<selected depth-1 IDs>)
  AND target IN (<owner impact targets>)
  AND kind IN (<modify-impact kinds>)
```

- Choose one deterministic direct edge per source using `(line is null, line, col, kind, target)`.
- Use a positive edge line as `edge_site`; otherwise use node `start_line`/`start_column` as `node_definition`.
- Normalize file separators to `/`.
- Populate `impact_witnesses` while leaving `impacted_node_ids` unchanged.

**Step 6: Run focused tests**

Run:

```powershell
pytest tests/unit/test_codegraph_adapter.py tests/unit/test_candidate_aggregation.py -q
```

Expected: PASS. Candidate aggregate counts remain unchanged.

**Step 7: Commit**

```powershell
git add pebra/core/models.py pebra/adapters/codegraph_adapter.py tests/unit/test_codegraph_adapter.py
git commit -m "feat: retain bounded impact witnesses"
```

## Milestone 2: Surface Witnesses Through Production Explanations

**Files:**

- Modify: `pebra/core/assessment_builder.py:214`
- Modify: `pebra/core/explanation_generator.py:89`
- Modify: `tests/unit/test_assessment_builder.py`
- Modify: `tests/unit/test_explanation_generator.py`
- Modify: the existing decision-engine or assessment-controller test module that already builds comparable graph-backed assessments

**Step 1: Write failing serialization tests**

Add an assessment-builder test asserting that `scores.symbol_scope_evidence.symbol_fanin` contains a globally bounded `impact_witnesses` list with:

```python
{
    "owner_qualified_name": "pkg.changed",
    "dependent_qualified_name": "pkg.caller",
    "file_path": "src/caller.py",
    "line": 42,
    "column": 7,
    "edge_kind": "calls",
    "depth": 1,
    "location_source": "edge_site",
}
```

Do not serialize internal node IDs.

Add tests for:

- no witness field when no trusted witnesses exist;
- a maximum of five serialized witnesses;
- deterministic ordering across reversed owner order;
- valid fallback entries with missing line/column.

**Step 2: Write failing explanation tests**

Assert that explanation generation adds concise grounded lines such as:

```text
Impact witness: pkg.caller in src/caller.py:42 calls changed symbol pkg.changed.
```

For a transitive witness:

```text
Impact witness: pkg.indirect in src/indirect.py:18 is reachable from changed symbol pkg.changed at dependency depth 2.
```

Also assert:

- depth-1 `edge_site` copy identifies the edge occurrence, for example "calls ... at file:line";
- every fallback says "reachable at depth N" and labels the location as the dependent definition;
- no fallback sentence implies that its definition location is an observed call/edge site or a complete path;
- at most five witness lines;
- no node IDs are rendered;
- no witness line is added when the graph is stale, unresolved, or absent;
- ordinary existing explanation lines remain unchanged when witnesses are absent.

**Step 3: Write the decision-invariance regression**

Build two otherwise identical assessments, one with witnesses and one without. Compare the decision-driving fingerprint:

```python
def _decision_fingerprint(result):
    return {
        "recommended_decision": result.recommended_decision,
        "requires_confirmation": result.requires_confirmation,
        "risk_mode": result.risk_mode,
        "action_status": result.action_status,
        "gates_fired": result.gates_fired,
        "decision_reason": result.decision_reason,
        "expected_loss": result.scores["expected_loss"],
        "benefit": result.scores["benefit"],
        "expected_utility": result.scores["expected_utility"],
        "utility_sd": result.scores["utility_sd"],
        "rau": result.scores["rau"],
    }
```

Assert both fingerprints are equal. Separately assert the explanation-only witness field differs.

**Step 4: Run tests and verify failure**

Run:

```powershell
pytest tests/unit/test_assessment_builder.py tests/unit/test_explanation_generator.py -q
pytest tests/unit -k "witness and decision" -q
```

Expected: FAIL because witnesses are not serialized or rendered.

**Step 5: Implement the pure projections**

- Add a small deterministic projection helper in `assessment_builder.py`.
- Read `OwnerRiskEvidence.impact_witnesses`; never query the graph from core.
- Apply the five-witness candidate cap.
- Add the projected list as a sibling under `symbol_fanin`.
- In `explanation_generator.py`, render only the structured fields already in the assessment.
- Let the existing composition and `model_guidance_packet.advisory.why` plumbing carry the lines to JSON and CLI.
- Do not change `decision_engine.py`, `candidate_aggregation.py`, or score math.

**Step 6: Verify production surfaces**

Run:

```powershell
pytest tests/unit/test_assessment_builder.py tests/unit/test_explanation_generator.py tests/unit/test_assess_controller.py tests/unit/test_composition.py tests/unit/test_cli_assess_card.py tests/golden/test_assess_cli_golden.py -q
```

Expected:

- PASS.
- The existing no-graph golden remains byte-for-byte unchanged.
- A separate graph-backed CLI/card test contains the structured witness and matching `why` line.
- Decision and numeric scores match the same graph-backed fixture without witnesses.
- Do not approve an existing golden snapshot update merely because the feature changed; churn in the
  no-graph golden indicates an explanation fallback regression.

**Step 7: Commit**

```powershell
git add pebra/core/assessment_builder.py pebra/core/explanation_generator.py tests/unit
git commit -m "feat: explain graph impact with bounded witnesses"
```

## Milestone 3: Expose the Shipped Witness in Real Experiment Arms

**Files:**

- Modify: `e2e/experiments/agent_ab/tools/advisory_check_real.py:151`
- Modify: `e2e/experiments/agent_ab/tests/test_advisory_shape.py:22`
- Modify: `e2e/experiments/agent_ab/tests/test_blinding.py`

**Step 1: Write failing model-facing projection tests**

Create a raw assess fixture containing structured impact witnesses and assert:

- real advisory text includes bounded dependent path/symbol/line/edge information;
- all real advisory decisions can carry the witness, including `proceed`;
- the top-level key order and `detail` shape still match sham;
- internal node IDs and host provenance are absent;
- no experiment-forbidden vocabulary appears;
- malformed, oversized, absolute-path, or non-string witness fields are ignored;
- edge kinds outside the production set `calls`, `references`, `instantiates`, `implements`, and
  `extends` are ignored;
- duplicate witness entries are emitted once;
- output with no witnesses is byte-for-byte equal to the prior hand-written advisory output.

**Step 2: Run tests and verify failure**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_advisory_shape.py e2e/experiments/agent_ab/tests/test_blinding.py -q
```

Expected: FAIL because `_shape_output()` currently discards impact witnesses.

**Step 3: Implement a strict blinded projector**

Add a pure `_impact_witness_text(result)` helper that:

- reads only `scores.symbol_scope_evidence.symbol_fanin.impact_witnesses`;
- validates repository-relative paths;
- accepts only the five production modify-impact edge kinds: `calls`, `references`, `instantiates`,
  `implements`, and `extends`;
- bounds to five entries and bounds each string length;
- never forwards node IDs or arbitrary free-form text;
- maps structured fields into arm-neutral sentences;
- returns an empty string for missing or invalid data.

Append its result in `_advisory_text()` after existing decision and safer-route text. Do not parse `why` sentences and do not add keys to `detail`.

**Step 4: Verify every real arm uses the projection**

Add or extend arm-backend tests proving:

- `pebra`, `pebra_graph_context`, `pebra_graph_repair`, `pebra_human_review`, and legacy `treatment`
  all call `advisory_check_real.advise`;
- the witness text survives the complete `_shape_output` -> `_advisory_backend` ->
  `with_candidate_patch` -> `tool_impl.advisory_check` chain;
- sham and graph-context controls retain their existing backends.

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_advisory_shape.py e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_run_trial.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add e2e/experiments/agent_ab/tools/advisory_check_real.py e2e/experiments/agent_ab/tests/test_advisory_shape.py e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_run_trial.py
git commit -m "test: align assay advisory with impact witnesses"
```

## Milestone 4: Fail Preflight When Witness Treatment Is Missing

**Files:**

- Modify: `e2e/experiments/agent_ab/runners/preflight.py:405`
- Modify: `e2e/experiments/agent_ab/tests/test_preflight.py:267`

**Step 1: Extend preflight fixtures**

Add a valid structured impact witness to `_payload(...)`. Keep separate explicit fixtures for:

- witness missing;
- empty list despite positive reach;
- malformed fields;
- more than five serialized witnesses;
- witness path outside the repository;
- depth outside `1..3`;
- stale/unresolved graph.

**Step 2: Write failing treatment-integrity assertions**

For graph-backed risky specimens, require:

- existing fresh/resolved graph checks pass;
- the existing positive-reach and positive-expected-loss checks pass first;
- when that validated reach is greater than zero, `impact_witnesses` is a non-empty list;
- every witness satisfies the production bounds and contains a dependent symbol or repository-relative file;
- witness count is at most five.

Do not require witnesses when reach is zero or for safe specimens that intentionally bypass graph-risk
validation. The current risky graph-backed preflight already rejects zero reach before witness
validation, so a leaf-symbol task cannot fail for "missing witnesses" unless it first claims material
graph impact.

**Step 3: Run tests and verify failure**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_preflight.py -k "graph or witness" -q
```

Expected: FAIL for the new witness-integrity cases.

**Step 4: Implement minimal preflight validation**

Add a private pure validator called by `_graph_backed_failure()`. Return actionable host-only failures such as:

```text
JS4: graph-backed treatment omitted bounded impact witnesses
```

Do not expose these messages to the subject.

**Step 5: Verify**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_preflight.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add e2e/experiments/agent_ab/runners/preflight.py e2e/experiments/agent_ab/tests/test_preflight.py
git commit -m "test: require witness-backed assay treatment"
```

## Milestone 5: Record Host-Only Witness Exposure

**Files:**

- Modify: `e2e/experiments/agent_ab/runners/run_pair.py:168`
- Modify: `e2e/experiments/agent_ab/runners/agent_loop.py:647`
- Modify: `e2e/experiments/agent_ab/models.py:124`
- Modify: `e2e/experiments/agent_ab/metrics/oracle.py:90`
- Modify: relevant runner, oracle, artifact, and observatory aggregate tests

**Step 1: Write failing telemetry tests**

Assert that a successful real advisory records a host-only receipt:

```python
{
    "assessment_id": "asm_7",
    "version": "impact-witness-v1",
    "count": 2,
    "delivered": True,
}
```

Also assert:

- zero-witness real results record `count == 0`;
- failed/subprocess-timeout advisories do not claim delivery;
- receipts are retained in `SubjectResult` and `RunOutcome`;
- receipt metadata is not added to model-facing advisory output;
- scorecard results are unchanged when receipts are present.

**Step 2: Run tests and verify failure**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_run_pair.py e2e/experiments/agent_ab/tests/test_agent_loop.py e2e/experiments/agent_ab/tests/test_oracle.py e2e/experiments/agent_ab/tests/test_run_artifacts.py e2e/experiments/agent_ab/tests/test_scorecard.py -q
```

Expected: FAIL because witness receipts are not modeled.

**Step 3: Implement minimal telemetry**

- Add `impact_witness_receipts` to `ArmTelemetry`, `SubjectResult`, and `RunOutcome`.
- Extract only count and treatment version from `AdvisoryOutput.raw_payload` after scope validation succeeds.
- Bind each receipt to the assessment ID.
- Copy receipts through result construction and artifact serialization.
- Keep raw witness content in the already captured advisory tool record; do not duplicate it in telemetry.
- Do not create a "model used witness" classifier.
- Do not add witness counts to endpoint aggregation.

**Step 4: Verify scoring invariance**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_run_pair.py e2e/experiments/agent_ab/tests/test_agent_loop.py e2e/experiments/agent_ab/tests/test_oracle.py e2e/experiments/agent_ab/tests/test_run_artifacts.py e2e/experiments/agent_ab/tests/test_scorecard.py e2e/experiments/agent_ab/tests/test_scorecard_assay.py -q
```

Expected: PASS with identical endpoint values.

**Step 5: Commit**

```powershell
git add e2e/experiments/agent_ab/models.py e2e/experiments/agent_ab/runners/run_pair.py e2e/experiments/agent_ab/runners/agent_loop.py e2e/experiments/agent_ab/metrics/oracle.py e2e/experiments/agent_ab/tests
git commit -m "test: record impact witness exposure"
```

## Milestone 6: Authenticate the New Treatment and Document Its Estimand

**Files:**

- Modify: `e2e/experiments/agent_ab/tools/advisory_contract.py:23`
- Modify: `e2e/experiments/agent_ab/runners/orchestrator.py:64`
- Modify: `e2e/experiments/agent_ab/tests/test_assay_design.py`
- Modify: orchestrator/design-hash tests
- Modify: `e2e/experiments/agent_ab/README.md:88`
- Modify: top-level `README.md` graph-grounding explanation only if Milestone 1 changes a user-facing claim

**Step 1: Write failing design-identity tests**

Add:

```python
ADVISORY_TREATMENT_VERSION = "impact-witness-v1"
```

Tests must prove:

- the version is present in `experiment_design`;
- it participates in `experiment_design_sha256`;
- run metadata carries both the unchanged lifecycle version and the independent advisory treatment
  version under distinct keys;
- `_authenticated_design_identity()` rejects a stale or missing version;
- changing the version requires a fresh run ID;
- cognitive lifecycle and gate-reason versions remain unchanged because their contracts did not change.

**Step 2: Run tests and verify failure**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_assay_design.py e2e/experiments/agent_ab/tests/test_orchestrator.py e2e/experiments/agent_ab/tests/test_run_artifacts.py -q
```

Expected: FAIL because the advisory treatment is not explicitly versioned.

**Step 3: Add treatment identity**

- Define the constant in `advisory_contract.py`.
- Add `advisory_treatment_version` to `_experiment_design()`.
- Validate it in `_authenticated_design_identity()`.
- Do not repurpose `GATE_REASON_TREATMENT_VERSION`.
- Do not bump `cognitive-lifecycle-v4`; subject instructions are unchanged.

**Step 4: Update experiment documentation**

Document:

- real PEBRA arms now receive up to five repository-native impact witnesses;
- the witness is explanation-only in production and does not change scores or decisions;
- the experiment tests the complete shipped PEBRA governance intervention, not a pure verdict-only factor;
- existing `blast_radius` is not a witness-ablation arm;
- witness delivery/count is process telemetry, not efficacy;
- old and new treatment versions cannot be pooled;
- lifecycle version identifies subject instructions, while advisory treatment version identifies the
  content delivered by the advisory; equality or coordinated bumps are not implied;
- this work enriches the current multi-arm assay and is not the proposed two-arm v5 redesign;
- a dedicated witness-ablation arm is deferred until there is a specific causal mechanism question and sufficient independent tasks.

**Step 5: Run tests**

Run:

```powershell
pytest e2e/experiments/agent_ab/tests/test_assay_design.py e2e/experiments/agent_ab/tests/test_orchestrator.py e2e/experiments/agent_ab/tests/test_run_artifacts.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add e2e/experiments/agent_ab/tools/advisory_contract.py e2e/experiments/agent_ab/runners/orchestrator.py e2e/experiments/agent_ab/tests e2e/experiments/agent_ab/README.md README.md
git commit -m "docs: version impact witness treatment"
```

Only stage `README.md` if it actually changed.

## Milestone 7: Full Verification and Review Gate

**Files:**

- No planned source changes unless verification finds a real defect.

**Step 1: Run focused production and experiment suites**

```powershell
pytest tests/unit/test_codegraph_adapter.py tests/unit/test_candidate_aggregation.py tests/unit/test_assessment_builder.py tests/unit/test_explanation_generator.py tests/unit/test_assess_controller.py tests/unit/test_composition.py tests/unit/test_cli_assess_card.py tests/golden/test_assess_cli_golden.py -q
pytest e2e/experiments/agent_ab/tests/test_advisory_shape.py e2e/experiments/agent_ab/tests/test_blinding.py e2e/experiments/agent_ab/tests/test_preflight.py e2e/experiments/agent_ab/tests/test_assay_design.py e2e/experiments/agent_ab/tests/test_run_pair.py e2e/experiments/agent_ab/tests/test_agent_loop.py e2e/experiments/agent_ab/tests/test_oracle.py e2e/experiments/agent_ab/tests/test_scorecard.py e2e/experiments/agent_ab/tests/test_scorecard_assay.py -q
```

Expected: PASS.

**Step 2: Run repository gates**

```powershell
nox -s tests
nox -s lint
nox -s e2e-fast
```

Expected: PASS.

**Step 3: Run the unpaid assay preflight**

Use the documented `assay_js --preflight-only` command from `e2e/experiments/agent_ab/README.md`.

Expected:

- the known risky specimen produces at least one valid bounded witness;
- graph scope, route calibration, candidate verification, and hidden-oracle checks pass;
- no provider API call occurs.

**Step 4: Review the final diff**

Review specifically for:

- accidental decision/scoring reads of `impact_witnesses`;
- unbounded SQL or path enumeration;
- edge citation against raw owners instead of modify-impact targets;
- exposure of node IDs or engine vocabulary;
- treatment projection omitted from any real advisory arm;
- new endpoint or causal claims based on witness count;
- accidental edits to the pre-existing experiment gate-contract work.

**Step 5: Stop before external actions**

Report:

- focused and full test results;
- preflight result;
- exact commits;
- any residual language-tier limitation.

Do not push, merge, tag, release, publish to PyPI, or run a paid assay without separate user authorization.

## Expected Effort

- Production model/adapter and explanation: medium, approximately one focused engineering day.
- Experiment projection, preflight, telemetry, and versioning: small-to-medium, approximately half to one day.
- Verification and review: approximately half a day.
- Total: roughly two to two-and-a-half focused days. Reserve the extra half day for cross-language
  modify-impact target joins, graph-backed CLI coverage, and preflight edge cases.
