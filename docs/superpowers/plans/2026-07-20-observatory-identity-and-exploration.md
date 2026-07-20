# PEBRA Observatory Identity and Repository Exploration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make assessment history human-identifiable, keep repeated audit events honest, prevent trusted graph evidence from drifting behind the repository, and teach agents to obtain bounded repository context through a provider-neutral `pebra explore` surface without weakening PEBRA's gate or read-only Observatory.

**Architecture:** Preserve the append-only, hash-chained assessment ledger. Derive display identity from hash-covered assessment content, add a hash-covered assessment timestamp for new rows, and keep grouping strictly inside the TUI. Before any trusted graph read, reconcile one already-initialized same-worktree index and fence it with repository-HEAD and graph-config digests; then reuse that prepared snapshot for the command lifetime. Add repository exploration as a small port/adapter/CLI use case; exploration is descriptive context and never authorization or trusted scoring evidence.

**Tech Stack:** Python 3.11+, SQLite, Textual 8.x, CodeGraph 1.1.1, argparse, pytest, pytest-textual-snapshot, nox, import-linter, GitHub Actions.

## Global Constraints

- The assessment table remains append-only; never delete, overwrite, or deduplicate audit rows.
- Preserve verification of every existing legacy hash chain. Missing new fields on legacy rows are valid.
- `assessed_commit` and the repository's current Git `HEAD` are separate facts. Never label one as the other.
- Raw assessment history is the default TUI view. Grouping is optional, contiguous, reversible, and presentation-only.
- A row without a validated exact candidate binding has no trustworthy candidate fingerprint and is never grouped.
- Exploration output is untrusted descriptive context. It never enters scores, gates, sanctions, candidate binding, verification, learning, or promotion.
- `pebra tui` remains read-only with respect to the PEBRA store. No graph work runs in its five-second SQLite refresh.
- CodeGraph `status` cleanliness alone is not proof that its index matches the current commit or `codegraph.json`. Trusted graph evidence requires a successful same-worktree reconcile plus stable HEAD/config fences.
- `assess`, `verify`, `dependents`, `graph-stats`, `capabilities`, `explore`, and an explicit TUI exploration action may reconcile an existing same-worktree derived graph cache. They never install, initialize, repair a borrowed index, or alter repository source/configuration.
- No graph subprocess runs from TUI mount, row selection, automatic refresh, or background polling. A user-triggered graph action must visibly distinguish preparation from the subsequent read-only query.
- Never generate or overwrite `codegraph.json` automatically. CodeGraph's built-in exclusions remain authoritative; project-specific `exclude` entries are operator-owned inputs whose raw-byte digest is recorded as graph-scope provenance.
- Graph-derived learning facts must not pool observations across different graph-scope digests.
- Do not add `--provider`, provider discovery, plugin manifests, or configuration registries while only one exploration adapter exists.
- Do not import `pebra.composition` from `pebra.tui`; construct and inject dependencies from the CLI/composition boundary.
- Do not run `nox -s e2e-ab`, tag, publish, or start a release workflow without a separate explicit maintainer authorization.
- At every milestone, run focused E2E behavior tests before review. Run the complete deterministic experiment suite only in Milestone 7.
- Update `docs/PEBRA_COMMAND_REFERENCE.md` in the same milestone that adds or changes any public command, flag, TUI key, developer entrypoint, environment variable, or shell-specific invocation. Never defer a newly shipped command to a later documentation-only milestone.
- Keep product invocations shell-neutral. Label neutral examples `console`; label only genuinely shell-specific activation, environment-variable, piping, path, globbing, or command-substitution examples `powershell`, `cmd`, or `bash`, with supported-shell equivalents where the syntax materially differs.
- Verify command-reference parity against the live argparse tree (`pebra help --all` plus targeted subcommand help) and `nox --list`; planned commands must remain explicitly labelled unshipped until their implementation milestone lands.
- Every milestone ends with an explicit review stop. Do not begin the next milestone until the maintainer approves it.

## Locked Vocabulary

```python
TargetProvenance = Literal[
    "candidate_bound",
    "declared",
    "legacy_guidance",
    "legacy_graph",
    "unavailable",
]

ExplorationStatus = Literal[
    "available",
    "unavailable",
    "stale",
    "unsupported",
    "error",
]

GraphSnapshotStatus = Literal[
    "available",
    "unavailable",
    "stale",
    "error",
]
```

The Observatory exposes three distinct concepts:

- `declared_files`: normalized `request.revision_envelope.expected_files`—the intended assessment scope;
- `bound_files`: validated keys from `model_guidance_packet.binding.candidate.files`—the exact materialized candidate;
- `target_files`: `bound_files` when available, otherwise `declared_files`, followed only by explicitly labelled legacy fallbacks.

The full 64-character candidate fingerprint is the SHA-256 of canonical JSON for the validated candidate binding. The ledger may display its first 10 characters, but grouping and machine-readable output always use the full digest.

---

## Milestone 1 — Honest Assessment Identity Projection

### Deliverable

New and legacy assessment summaries expose task, action, target files, target provenance, and an exact candidate fingerprint when one is genuinely recoverable. The web dashboard and TUI consume the same projected fields.

### Files

- Create: `pebra/core/assessment_history.py`
- Modify: `pebra/adapters/store/db.py:1420-1453`
- Modify: `pebra/app/observatory_query_controller.py:31-35`
- Modify: `pebra/ports/observatory_read_port.py`
- Test: `tests/unit/test_assessment_history.py`
- Test: `tests/integration/test_store_read_api.py`
- Test: `tests/unit/test_observatory_query_controller.py`
- Test: `tests/integration/test_tui_data.py`
- Test: `tests/integration/test_dashboard_server.py`

### Interfaces

Produces:

```python
@dataclass(frozen=True)
class AssessmentHistoryIdentity:
    task: str | None
    action_id: str | None
    declared_files: tuple[str, ...]
    bound_files: tuple[str, ...]
    target_files: tuple[str, ...]
    target_provenance: TargetProvenance
    candidate_fingerprint: str | None

def project_assessment_identity(content: Mapping[str, Any]) -> AssessmentHistoryIdentity:
    """Project trustworthy display identity from one hash-covered assessment payload."""
```

The existing `ObservatoryReadPort.list_assessments()` signature is unchanged. Its returned row dictionary gains the fields above; no additional SQL query or port method is introduced.

### TDD steps

- [x] **Step 1: Write projection tests before implementation**

Cover these exact cases in `tests/unit/test_assessment_history.py`:

- `test_exact_candidate_binding_wins_for_display_target`
- `test_revision_envelope_is_authoritative_declared_scope`
- `test_legacy_guidance_scope_is_labelled_inferred`
- `test_graph_resolved_paths_are_last_legacy_fallback`
- `test_symbol_ids_are_not_misrepresented_as_file_paths`
- `test_invalid_binding_algorithm_has_no_fingerprint`
- `test_invalid_file_digest_has_no_fingerprint`
- `test_fingerprint_is_stable_across_dictionary_order`
- `test_paths_are_forward_slash_normalized_and_deduplicated`
- `test_missing_scope_is_unavailable_not_empty_declared_scope`

- [x] **Step 2: Run the projection tests and confirm the expected red state**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_assessment_history.py -q
```

Expected: collection fails because `pebra.core.assessment_history` does not exist.

- [x] **Step 3: Implement the pure projection**

Use only standard-library imports. Validate the candidate binding as:

```python
binding = ((content.get("model_guidance_packet") or {}).get("binding") or {}).get("candidate")
valid = (
    isinstance(binding, dict)
    and binding.get("algorithm") == CANDIDATE_BINDING_ALGORITHM
    and isinstance(binding.get("files"), dict)
    and bool(binding["files"])
    and all(
        isinstance(path, str)
        and path
        and isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest)
        for path, digest in binding["files"].items()
    )
)
```

Compute the fingerprint only for `valid` bindings:

```python
canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Do not parse `src/auth.py::validate_login` as a file. A legacy symbol may contribute a path only when a separate persisted `file_path` or graph `resolved_file_paths` field names it.

- [x] **Step 4: Run pure tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_assessment_history.py -q
```

Expected: all projection tests pass.

- [x] **Step 5: Extend the store summary projection**

In `SqliteStore.list_assessments()`, call `project_assessment_identity(content)` once per row and add:

```python
"task": identity.task,
"action_id": identity.action_id,
"declared_files": list(identity.declared_files),
"bound_files": list(identity.bound_files),
"target_files": list(identity.target_files),
"target_provenance": identity.target_provenance,
"candidate_fingerprint": identity.candidate_fingerprint,
```

Do not remove or rename existing keys.

- [x] **Step 6: Lock storage and surface parity behavior**

Add integration assertions for:

- a modern assessment containing both declared and candidate-bound scope;
- a legacy assessment with no revision envelope;
- a malformed binding that remains visible but has `candidate_fingerprint=None`;
- FastAPI JSON and `ObservatoryData.refresh_snapshot()` returning identical row fields;
- repo scoping remaining unchanged.

- [x] **Step 7: Run the milestone behavior gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_assessment_history.py tests\integration\test_store_read_api.py tests\unit\test_observatory_query_controller.py tests\integration\test_tui_data.py tests\integration\test_dashboard_server.py -q
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
.\.venv\Scripts\ruff.exe check pebra\core\assessment_history.py pebra\adapters\store\db.py tests\unit\test_assessment_history.py
.\.venv\Scripts\lint-imports.exe
```

Expected: tests pass, Ruff reports no violations, and every import contract is kept.

- [x] **Step 8: Commit the independently reviewable milestone**

```powershell
git add pebra/core/assessment_history.py pebra/adapters/store/db.py pebra/app/observatory_query_controller.py pebra/ports/observatory_read_port.py tests/unit/test_assessment_history.py tests/integration/test_store_read_api.py tests/unit/test_observatory_query_controller.py tests/integration/test_tui_data.py tests/integration/test_dashboard_server.py
git commit -m "feat: expose honest assessment identity"
```

### STOP FOR REVIEW 1

Report the projection precedence, legacy degradation cases, focused-test results, E2E boundary result, and import-contract result. Do not start Milestone 2 without approval.

---

## Milestone 2 — Hash-Covered Assessment Time

### Deliverable

Every new assessment records an immutable UTC `assessed_at` inside its hash-covered content. Legacy rows remain valid and display time as unavailable.

### Files

- Modify: `pebra/adapters/store/db.py:36-52`
- Modify: `pebra/adapters/store/db.py:682-721`
- Modify: `pebra/core/assessment_history.py`
- Test: `tests/integration/test_store_hash_chain.py`
- Test: `tests/integration/test_store_read_api.py`
- Test: `tests/unit/test_assessment_history.py`

### Interfaces

Modify the private canonicalizer to make the persistence timestamp explicit:

```python
def _canonical(
    result: AssessmentResult,
    request_payload: dict[str, Any],
    *,
    assessed_at: str | None = None,
) -> str:
    """Return the canonical hash-chain payload; omit assessed_at for legacy fixtures."""
```

When `assessed_at is None`, omit the key. This preserves fixtures that intentionally construct legacy canonical rows. New `persist_assessment()` calls always pass the single UTC `recorded_at` generated before `BEGIN IMMEDIATE`.

### TDD steps

- [x] **Step 1: Add failing timestamp tests**

Prove these exact behaviors:

- `test_new_assessment_persists_hash_covered_assessed_at`
- `test_tampering_with_assessed_at_breaks_assessment_chain`
- `test_legacy_row_without_assessed_at_still_validates`
- `test_list_projection_returns_assessed_at_or_none`
- `test_assessed_at_is_utc_iso_8601`

- [x] **Step 2: Confirm tests fail before implementation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_store_hash_chain.py tests\integration\test_store_read_api.py -q
```

Expected: the new assertions fail because assessment content has no `assessed_at`.

- [x] **Step 3: Persist one timestamp atomically**

Change the new-row path to:

```python
recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
content_json = _canonical(result, request_payload, assessed_at=recorded_at)
```

Use that same `recorded_at` for prediction rows created in the transaction. Do not derive time from the auto-increment ID, database file modification time, outcomes, or predictions when reading legacy rows.

- [x] **Step 4: Expose `assessed_at` through the history identity and list projection**

Add `assessed_at: str | None` to `AssessmentHistoryIdentity`. Accept only a non-empty string that parses through `datetime.fromisoformat()`; malformed legacy content displays unavailable but remains chain-verifiable.

- [x] **Step 5: Run chain and compatibility tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_store_hash_chain.py tests\integration\test_store_read_api.py tests\unit\test_assessment_history.py -q
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
.\.venv\Scripts\ruff.exe check pebra\adapters\store\db.py pebra\core\assessment_history.py
```

Expected: new and legacy chains pass; deliberate timestamp tampering fails validation.

- [x] **Step 6: Commit**

```powershell
git add pebra/adapters/store/db.py pebra/core/assessment_history.py tests/integration/test_store_hash_chain.py tests/integration/test_store_read_api.py tests/unit/test_assessment_history.py
git commit -m "feat: timestamp assessment audit events"
```

### STOP FOR REVIEW 2

Report the exact stored timestamp format, legacy behavior, chain-tamper proof, and focused E2E result. Do not start Milestone 3 without approval.

---

## Milestone 3 — Target-Visible, Commit-Honest Observatory

### Deliverable

The TUI identifies what was assessed without calling an assessed commit live `HEAD`. It remains usable at 70, 80, 100, and 120 columns.

### Files

- Modify: `pebra/tui/widgets/status_header.py`
- Modify: `pebra/tui/widgets/ledger_table.py`
- Modify: `pebra/tui/screens/observatory.py`
- Modify: `pebra/tui/screens/detail.py`
- Modify: `pebra/tui/theme.tcss`
- Test: `tests/unit/test_tui_rau_lane.py`
- Test: `tests/integration/test_tui_app.py`
- Test: `tests/integration/test_tui_detail.py`
- Test: `tests/integration/test_tui_refresh.py`
- Test: `tests/snapshots/test_tui_snapshots.py`
- Update: `tests/snapshots/__snapshots__/test_tui_snapshots/*.svg`

### Locked layout

- Header: `repo <slug> · latest assessed <sha> · store chain <state> · <N> asm`.
- Never show `HEAD` unless a separately measured current Git value is added in a later approved design.
- Wide (`>=120`): ID, target, task, assessed commit, gate lane, decision, RAU, expected loss, benefit, status, assessed time.
- Normal (`80-119`): ID, target, assessed commit, decision, RAU, status.
- Narrow (`<80`): ID, target, decision, RAU.
- `target` renders `filename` or `filename +N`; full normalized paths and provenance appear in detail.
- `task` is display-bounded by `format_task`; the hash-covered underlying task is never truncated or rewritten.
- Missing target renders `target unavailable`, never a guessed path.

### TDD steps

- [x] **Step 1: Add failing rendering and snapshot cases**

Cover these exact behaviors:

- `test_status_calls_commit_latest_assessed_not_head`
- `test_single_target_uses_compact_filename`
- `test_multiple_targets_render_filename_plus_count`
- `test_unavailable_target_is_explicit`
- `test_detail_lists_declared_and_bound_files_separately`
- `test_detail_labels_legacy_inference`
- `test_resize_rebuild_preserves_selected_assessment`
- `test_breakpoint_rebuild_resets_horizontal_scroll`
- `test_task_display_is_bounded_without_mutating_row_data`
- `test_refresh_preserves_horizontal_scroll_and_focus`

Add settled snapshots at 70, 80, 100, and 120 columns.

- [x] **Step 2: Verify the tests fail on the current target-blind ledger**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_tui_app.py tests\integration\test_tui_detail.py tests\snapshots\test_tui_snapshots.py -q
```

Expected: target columns/detail fields are missing and the old header still contains `HEAD`.

- [x] **Step 3: Implement compact display helpers**

Add pure helpers in `ledger_table.py`:

```python
def format_target(paths: Sequence[str]) -> str:
    if not paths:
        return "target unavailable"
    first = PurePosixPath(paths[0]).name or paths[0]
    return first if len(paths) == 1 else f"{first} +{len(paths) - 1}"

def format_assessed_at(value: str | None) -> str:
    return value[:16].replace("T", " ") if value else "—"

def format_task(value: str | None, *, width: int = 28) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "—"
    return text if len(text) <= width else f"{text[: width - 1]}…"
```

Do not truncate underlying row data—only display cells.

- [x] **Step 4: Implement breakpoint-specific column sets**

Rebuild columns only when crossing a breakpoint, not on every resize event. Preserve the selected assessment ID and focus, but reset horizontal scroll to `0` because the old column coordinate is meaningless after a column-set rebuild. A normal five-second data refresh does not rebuild columns and must preserve the selected ID, focus, and both scroll axes.

- [x] **Step 5: Expand detail without graph calls**

Render task, action ID, assessment time, assessed commit, fingerprint, declared files, bound files, chosen display targets, and target provenance. Do not reconstruct gates or graph state.

- [x] **Step 6: Regenerate and inspect snapshots**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update
.\.venv\Scripts\python.exe -m pytest tests\snapshots -q
```

Expected: all baselines are deterministic and no 70/80-column snapshot wraps the status line or loses target/decision/RAU.

- [x] **Step 7: Run the milestone behavior gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_tui_app.py tests\integration\test_tui_detail.py tests\integration\test_tui_refresh.py tests\snapshots\test_tui_snapshots.py -q
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
```

Expected: full tests, snapshots, Ruff, and import contracts pass.

- [x] **Step 8: Commit**

```powershell
git add pebra/tui tests/integration/test_tui_app.py tests/integration/test_tui_detail.py tests/integration/test_tui_refresh.py tests/snapshots
git commit -m "feat: identify assessment targets in observatory"
```

### STOP FOR REVIEW 3

Provide the four width snapshots and explicitly confirm that no live Git call was introduced and the refresh preserves selection/focus/scroll. Do not start Milestone 4 without approval.

---

## Milestone 4 — Reversible Exact-Candidate Grouping

### Deliverable

Users can toggle a concise view of genuinely repeated candidates without losing or altering audit history. Raw history remains the default.

### Files

- Create: `pebra/tui/ledger_groups.py`
- Modify: `pebra/tui/screens/observatory.py`
- Modify: `pebra/tui/widgets/ledger_table.py`
- Modify: `pebra/tui/app.py`
- Modify: `pebra/tui/theme.tcss`
- Test: `tests/unit/test_tui_ledger_groups.py`
- Test: `tests/integration/test_tui_app.py`
- Test: `tests/integration/test_tui_detail.py`
- Test: `tests/integration/test_tui_refresh.py`
- Test: `tests/snapshots/test_tui_snapshots.py`

### Interfaces

```python
@dataclass(frozen=True)
class LedgerGroup:
    primary_assessment_id: str
    assessment_ids: tuple[str, ...]
    latest_row: Mapping[str, Any]

def group_contiguous_assessments(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[LedgerGroup, ...]:
    """Collapse only adjacent rows with one validated, identical semantic grouping key."""
```

The equality key is:

```python
(
    row["candidate_fingerprint"],
    row["assessed_commit"],
    row["decision"],
    row["terminal_status"],
    row["task"],
    row["action_id"],
    tuple(row["target_files"]),
    row["scores"].get("rau"),
    row["scores"].get("expected_loss"),
    row["scores"].get("benefit"),
)
```

If `candidate_fingerprint` is absent or invalid, the row gets a unique key and cannot collapse.

### TDD steps

- [x] **Step 1: Write adversarial grouping tests**

Prove these exact behaviors:

- `test_identical_contiguous_bound_candidates_group`
- `test_same_commit_and_decision_different_fingerprint_do_not_group`
- `test_same_candidate_different_scores_do_not_group`
- `test_same_candidate_different_task_does_not_group`
- `test_noncontiguous_repeat_does_not_cross_intervening_row`
- `test_legacy_unfingerprinted_rows_never_group`
- `test_group_preserves_every_assessment_id_in_order`

- [x] **Step 2: Run the new unit tests and confirm red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_tui_ledger_groups.py -q
```

Expected: collection fails because the grouping module does not exist.

- [x] **Step 3: Implement the pure contiguous grouper**

Use `itertools.groupby` only after assigning unique keys to unfingerprinted rows. Never mutate source dictionaries or reorder rows.

- [x] **Step 4: Add the TUI toggle**

Add product binding:

```python
("g", "toggle_grouping", "Group repeats")
```

Initial state is `group_repeats = False`. The footer changes between `g Group repeats` and `g Show raw`. A grouped row displays `×N`, opens the latest assessment on Enter, and shows all contained IDs in the detail screen. Toggling back restores the previously selected exact assessment when it still exists.

- [x] **Step 5: Keep counts and trends raw**

`overview.total`, status counts, decision counts, and sparklines continue to consume raw snapshot rows. Add a separate ledger caption such as `7 groups / 16 assessments` only while grouping is active.

- [x] **Step 6: Test refresh interactions**

Prove that automatic and manual refreshes preserve the grouping mode, selected underlying ID, focus, scroll, and open detail screen. A new repeated row may extend the newest group but must not select it automatically.

- [x] **Step 7: Run snapshots and E2E behavior gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_tui_ledger_groups.py tests\integration\test_tui_app.py tests\integration\test_tui_detail.py tests\integration\test_tui_refresh.py -q
.\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update
.\.venv\Scripts\python.exe -m pytest tests\snapshots e2e\test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
```

Expected: raw mode remains the default and every audit ID remains reachable.

- [x] **Step 8: Commit**

```powershell
git add pebra/tui tests/unit/test_tui_ledger_groups.py tests/integration/test_tui_app.py tests/integration/test_tui_detail.py tests/integration/test_tui_refresh.py tests/snapshots
git commit -m "feat: group repeated candidates in observatory"
```

### STOP FOR REVIEW 4

Report every negative grouping case and demonstrate raw/grouped screenshots. Do not start Milestone 5 without approval.

---

## Milestone 5 — Trusted Graph Snapshot and Provider-Neutral `pebra explore`

### Deliverable

PEBRA no longer treats a clean CodeGraph status as sufficient proof of freshness. Every trusted graph operation prepares one same-worktree snapshot fenced by stable repository HEAD and graph configuration, and agents/humans can then request bounded repository context, dependent files, and affected tests through one PEBRA command. CodeGraph is the first adapter but is not named in the public schema shape.

### Files

- Create: `pebra/core/graph_snapshot.py`
- Create: `pebra/core/exploration.py`
- Create: `pebra/ports/repository_explorer_port.py`
- Create: `pebra/adapters/codegraph_explorer.py`
- Create: `pebra/cli/explore.py`
- Modify: `pebra/adapters/codegraph_adapter.py`
- Modify: `pebra/adapters/codegraph_graph_reader.py`
- Modify: `pebra/app/assess_controller.py`
- Modify: `pebra/app/promotion_controller.py`
- Modify: `pebra/cli/setup_graph.py`
- Modify: `pebra/cli/dashboard.py`
- Modify: `pebra/dashboard/server.py`
- Modify: `pebra/cli/main.py`
- Modify: `pebra/composition.py`
- Modify: `docs/PEBRA_COMMAND_REFERENCE.md`
- Modify: `.importlinter`
- Test: `tests/unit/test_codegraph_freshness.py`
- Test: `tests/integration/test_codegraph_freshness_real.py`
- Test: `tests/unit/test_exploration.py`
- Test: `tests/unit/test_codegraph_explorer.py`
- Test: `tests/unit/test_cli_explore.py`
- Test: `tests/unit/test_cli_help.py`
- Test: `tests/unit/test_dependents.py`
- Test: `tests/unit/test_graph_stats_cli.py`
- Test: `tests/unit/test_capabilities_cli.py`
- Test: `tests/unit/test_codegraph_graph_reader.py`
- Test: `tests/unit/test_promotion_controller.py`
- Test: `tests/unit/test_cli_setup_graph.py`
- Test: `tests/integration/test_dashboard_server.py`
- Test: `tests/integration/test_dashboard_read_only_no_write.py`
- Test: `tests/integration/test_explore_cli.py`
- Test: `tests/integration/test_codegraph_explorer_real.py`
- Create: `e2e/test_graph_snapshot_boundary.py`

### Interfaces

```python
@dataclass(frozen=True)
class GraphSnapshot:
    status: GraphSnapshotStatus
    provider: str | None
    provider_version: str | None
    index_version: str | None
    repo_head: str | None
    config_digest: str
    graph_scope_digest: str | None
    sync_performed: bool
    fallback_reason: str | None

@dataclass(frozen=True)
class ExplorationResult:
    status: ExplorationStatus
    snapshot: GraphSnapshot
    context: str
    dependent_files: tuple[str, ...]
    affected_tests: tuple[str, ...]
    warnings: tuple[str, ...]
    fallback_reason: str | None
    truncated: bool

class RepositoryExplorer(Protocol):
    def prepare(self, repo_root: str) -> GraphSnapshot:
        """Reconcile an existing same-worktree provider index and return its fenced snapshot."""

    def explore(
        self,
        repo_root: str,
        query: str,
        *,
        snapshot: GraphSnapshot,
        files: tuple[str, ...] = (),
        max_files: int = 8,
        max_bytes: int = 24_000,
    ) -> ExplorationResult:
        """Return bounded descriptive repository context without authorizing an edit."""
```

CLI contract:

```text
pebra explore QUERY [--file PATH]... [--max-files N] [--max-bytes N]
                    [--repo-root PATH] [--json]
```

`QUERY` is required unless at least one `--file` is supplied. `--provider` is intentionally absent. The command may reconcile an already-initialized same-worktree derived graph index; help and human output say so. It never installs an engine, creates an index, repairs a worktree mismatch, or edits `codegraph.json`.

### TDD steps

- [x] **Step 1: Write false-fresh regression tests before implementation**

Use a fake runner for exact control-flow tests and a temporary real Git repository for the pinned-provider tests. Cover:

- an index built at commit A followed by a clean checkout to commit B containing an added, modified, and deleted source file;
- adding, changing, and removing only `codegraph.json` while source files remain unchanged;
- initial status reporting zero pending changes in both scenarios;
- same-worktree preparation still invoking exactly one `sync` and returning data/config provenance for B;
- an uninitialized index and a worktree mismatch never invoking `sync`;
- sync timeout/non-zero exit never falling back to the initial apparently-clean status;
- HEAD or config bytes changing during preparation causing one retry, then `status="stale"` if the second fence also moves;
- Windows `.cmd` launcher resolution.

The real-provider test must assert both the newly added symbol is present and the deleted symbol is absent after preparation. For pinned CodeGraph 1.1.1, exercise supported `codegraph.json` fields (`extensions` and `includeIgnored`) across add/change/remove transitions and prove that each raw-byte config change participates in the fence. Do not claim or emulate `exclude` support: the managed 1.1.1 distribution does not implement it, even though the newer unreleased reference checkout does.

- [x] **Step 2: Run the freshness tests and confirm the expected red state**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_codegraph_freshness.py tests\integration\test_codegraph_freshness_real.py -q
```

Expected: the clean-checkout/config-only cases fail because `_default_status()` currently skips sync when `status --json` looks clean.

- [x] **Step 3: Implement one bounded graph preparation boundary**

Split the current status helper into a non-mutating result parser and an explicit preparation operation. Preparation must implement this exact sequence, with at most two attempts:

```text
HEAD-before + raw codegraph.json digest-before
  -> initial status
  -> reject absent/uninitialized/worktree-mismatch
  -> codegraph sync <repo-root> (must exit 0)
  -> post-sync status (must be fresh)
  -> HEAD-after + config digest-after
  -> accept only when both fences are unchanged
```

Use `sha256(raw_bytes)` for an existing `codegraph.json` and the literal sentinel `absent` otherwise. Compute `graph_scope_digest` from canonical JSON containing provider, provider version, extraction/index version, and config digest; do not include commit SHA because the scope cohort must survive ordinary commits. Cache the accepted status for the lifetime of one adapter instance so one assessment does not sync once per action/provider call.

Do not retain the current fallback of returning the initial status after a failed sync. Failure returns an unavailable/stale snapshot and existing `require_graph` policy decides whether assessment may continue.

- [x] **Step 4: Wire the snapshot into trusted scoring and verification**

`build_assess_ports()` prepares one snapshot before constructing graph-backed ports and supplies that same cached adapter to fan-in, file roll-up, capability, and dependent-context reads. `assessed_commit` remains an independent Git read; trusted graph evidence is emitted only when it equals `snapshot.repo_head`.

Add `repo_head`, `config_digest`, and `graph_scope_digest` to graph provenance in `assess_controller.py`. Apply the same prepare-once rule to verification and to the existing `graph-stats`, `capabilities`, and `dependents` command boundaries. A normal `pebra dashboard` launch may prepare once and inject the resulting snapshot; `pebra dashboard --read-only` must not prepare and returns graph `available=False` unless a snapshot was explicitly injected by its caller. A GET route never prepares/syncs the graph.

- [x] **Step 5: Prevent learning across graph-scope regimes**

For graph-derived promotion candidates, require one non-empty `graph_scope_digest` across the matched rows. Mixed or missing/known scope digests veto that graph-derived promotion with a named reason; non-graph promotion candidates retain their existing behavior. Preserve provider/index/scope provenance in promoted facts. Add tests for one-scope success, mixed-scope veto, legacy-only rows, and proof that non-graph facts are unaffected.

- [x] **Step 6: Commit and review the safety half before adding exploration**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_codegraph_freshness.py tests\integration\test_codegraph_freshness_real.py tests\unit\test_promotion_controller.py -q
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
git add pebra/core/graph_snapshot.py pebra/adapters/codegraph_adapter.py pebra/adapters/codegraph_graph_reader.py pebra/app/assess_controller.py pebra/app/promotion_controller.py pebra/cli/dashboard.py pebra/dashboard/server.py pebra/composition.py tests/unit/test_codegraph_freshness.py tests/integration/test_codegraph_freshness_real.py tests/unit/test_codegraph_graph_reader.py tests/integration/test_dashboard_server.py tests/integration/test_dashboard_read_only_no_write.py tests/unit/test_promotion_controller.py
git commit -m "fix: bind graph evidence to reconciled repository state"
```

Expected: the false-fresh cases are closed before any new exploration surface exists. If this gate fails, stop Milestone 5.

- [x] **Step 7: Characterize preparation and query mutation separately**

Against the pinned CodeGraph version, first prepare the snapshot. Preparation may change only the resolved `.codegraph/` derived cache. Prove every repository path outside that cache—including `codegraph.json` and `.pebra/`—remains byte-identical. Then snapshot `.codegraph/` and run:

```powershell
codegraph explore "repository resolution" --path . --max-files 2
codegraph affected pebra/observatory_context.py --path . --json
```

Compare paths, sizes, mtimes, and SHA-256 values after the query phase. This test is pass/fail:

- if both query commands leave the prepared index byte-identical, record that evidence and allow implementation to continue;
- if either command writes or migrates index state, stop Milestone 5 and do not ship `pebra explore` until CodeGraph provides or PEBRA implements a proven non-mutating query path.

Execution finding and maintainer decision (2026-07-20): managed CodeGraph 1.1.1 opens query connections in WAL mode. Both `explore` and `affected` may transiently create and remove `codegraph.db-wal` and `codegraph.db-shm` inside the resolved `.codegraph` derived cache. The maintainer approved this narrower invariant: exploration may perform that transient SQLite housekeeping, but it must not change source files, `codegraph.json`, `.pebra`, Git content, graph schema, or logical graph data. Query execution must remain explicit (CLI or the M6 `x` action), never run from refresh/mount/GET/gate-hook paths, and any mutation outside the resolved cache or any logical graph/schema change fails the operation.

Before rerunning the gate, remove PEBRA's avoidable SQLite version probe: consume `status.index.builtWithExtractionVersion` from the already-validated post-sync status instead of opening `codegraph.db` merely to discover the extraction version. Then prove the persistent `codegraph.db` file and all paths outside the resolved cache remain byte-identical, the schema and logical graph rows remain identical, and the post-query HEAD/config/freshness fence still matches. Only `*-wal`/`*-shm` lifecycle and derived-cache directory metadata may vary.

Do not confuse this with freshness: byte-identical query behavior proves only that queries are non-mutating. The preceding preparation/fence tests prove that the queried snapshot is current.

- [x] **Step 8: Write pure result and bounds tests**

Prove max-files is clamped to `1..32`, max-bytes to `1_000..100_000`, output is UTF-8-safe, truncation is explicit, and unavailable results never fabricate files or tests.

- [x] **Step 9: Write adapter tests with a fake subprocess runner**

Lock the exact CodeGraph argv:

```python
[engine, "explore", query, "--path", repo_root, "--max-files", str(max_files)]
[engine, "affected", *files, "--path", repo_root, "--json"]
```

Cover missing engine, timeout, non-zero exit, malformed affected JSON, wrong JSON types, Windows path normalization, oversized stdout, duplicate files, and file-only mode. Lock the real `affected --json` schema to `changedFiles`, `affectedTests`, and `totalDependentsTraversed`; never pretend it returns dependent files.

- [x] **Step 10: Implement the adapter by reusing the existing dependency reader**

The `explore` output is bounded opaque context. Do not parse free-form prose into trusted symbols, source-file lists, or risk fields. The `affected --json` payload may populate `affected_tests` after structural validation. Populate `dependent_files` only through the existing `CodeGraphAdapter.dependent_files_result()` read path using the already-prepared cached status; do not create a second dependency implementation. Use `resolve_engine_argv()` for Windows `.cmd` support.

- [x] **Step 11: Implement the CLI without a one-caller application controller**

Follow the existing `pebra dependents` shape: CLI validation/clamping delegates through `composition` to one explorer instance, calls `prepare()` once, then calls `explore()` with that snapshot. Do not add `explore_controller.py`. Human output includes snapshot freshness/scope, bounded context, dependent files, affected tests, warnings, and fallback. JSON serializes the dataclasses without provider-specific keys.

Handled provider absence or staleness returns exit 0 with `status != available`; malformed user arguments return argparse exit 2; unexpected adapter contract violations return exit 1.

- [x] **Step 12: Preserve `pebra dependents` compatibility without rewiring it**

Leave `pebra dependents` on `composition.dependent_files_result()` and preserve its JSON/text shapes under characterization tests. Exploration reuses that adapter capability internally; it does not replace the shipped command or change its output.

- [x] **Step 13: Report graph configuration without scaffolding it**

Extend read-only `pebra doctor` output with: whether `codegraph.json` exists, its raw-byte digest/sentinel, the structurally valid fields supported by the active managed provider, and an explicit capability note that pinned CodeGraph 1.1.1 supports `extensions` and `includeIgnored` but not `exclude`. If an `exclude` key is present, report it as unsupported by the active provider rather than calling it effective. Malformed config is reported, not repaired. `setup-graph` must preserve an existing config byte-for-byte and run preparation after initialization. Do not add a scaffold flag, guessed exclusions, or PEBRA-owned filtering in this plan.

- [x] **Step 14: Add CLI/help/command-reference/import-contract coverage**

`pebra help`, `pebra help --all`, and `pebra help explore` must document every flag and the bounded existing-index reconciliation. Add an import contract proving `pebra.core.graph_snapshot`, `pebra.core.exploration`, and the port remain adapter-free and that CLI exploration code does not enter assessment scoring.

In the same commit that wires the parser, update `docs/PEBRA_COMMAND_REFERENCE.md`: change the verified root-command count from 21 to 22, remove the unshipped warning, add the complete `pebra explore` syntax/exit semantics/freshness boundary, and keep its examples shell-neutral. Add a regression assertion that the documented root-command inventory equals the live argparse command set, so a future addition, deletion, or rename cannot silently stale the reference.

- [x] **Step 15: Run focused and real-provider gates**

Add a subprocess E2E using a temporary fake graph-engine launcher selected through `PEBRA_CODEGRAPH_BIN`. It must prove an apparently-clean status is followed by sync before `assess`/`explore`, the emitted assessment provenance carries the fenced HEAD/scope digest, `explore` queries only after preparation, and `dashboard --read-only` never invokes the launcher. This is the milestone behavior proof; unit tests alone are not sufficient.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_codegraph_freshness.py tests\unit\test_exploration.py tests\unit\test_codegraph_explorer.py tests\unit\test_cli_explore.py tests\unit\test_cli_help.py tests\unit\test_dependents.py tests\unit\test_graph_stats_cli.py tests\unit\test_capabilities_cli.py tests\unit\test_codegraph_graph_reader.py tests\unit\test_promotion_controller.py tests\unit\test_cli_setup_graph.py tests\integration\test_dashboard_server.py tests\integration\test_dashboard_read_only_no_write.py tests\integration\test_explore_cli.py -q
.\.venv\Scripts\python.exe -m pytest tests\integration\test_codegraph_freshness_real.py tests\integration\test_codegraph_explorer_real.py -q
.\.venv\Scripts\python.exe -m pytest e2e\test_graph_snapshot_boundary.py e2e\test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint
```

Expected: clean commit/config transitions are reconciled; unavailable CodeGraph degrades structurally; the pinned real-provider case returns bounded current context or an explicit unavailable result; all import contracts pass.

- [x] **Step 16: Commit the exploration half**

```powershell
git add pebra/core/exploration.py pebra/ports/repository_explorer_port.py pebra/adapters/codegraph_explorer.py pebra/cli/explore.py pebra/cli/setup_graph.py pebra/cli/main.py pebra/composition.py .importlinter tests/unit/test_exploration.py tests/unit/test_codegraph_explorer.py tests/unit/test_cli_explore.py tests/unit/test_cli_help.py tests/unit/test_dependents.py tests/unit/test_graph_stats_cli.py tests/unit/test_capabilities_cli.py tests/unit/test_codegraph_graph_reader.py tests/unit/test_cli_setup_graph.py tests/integration/test_explore_cli.py tests/integration/test_codegraph_explorer_real.py e2e/test_graph_snapshot_boundary.py
git add -f docs/PEBRA_COMMAND_REFERENCE.md
git commit -m "feat: add provider-neutral repository exploration"
```

### STOP FOR REVIEW 5

Report the clean-checkout/config-only freshness proofs, HEAD/config fence behavior, graph-scope learning veto, explicit preparation mutations, byte-identical query experiment, reused dependency path, argv contracts, byte/file bounds, failure semantics, help output, and whether Milestone 6 TUI integration is permitted. Do not start Milestone 6 without approval.

---

## Milestone 6 — Agent Understand Phase and Optional TUI Impact

### Deliverable

Generated agent guidance teaches significant or unfamiliar edits to use already-supplied current repository context or, when none exists, `pebra explore` before assessment. The TUI can display provider-neutral impact only when M5 proved both preparation freshness and query non-mutation.

### Files

- Modify: `pebra/cli/agent_init.py`
- Modify: `pebra/core/agent_hosts.py`
- Modify: `docs/GATE_CONTRACT.md`
- Modify: `docs/PEBRA_COMMAND_REFERENCE.md`
- Modify: `pebra/cli/tui.py`
- Modify: `pebra/tui/app.py`
- Modify: `pebra/tui/data.py`
- Modify: `pebra/tui/screens/detail.py`
- Modify: `pebra/tui/theme.tcss`
- Modify: `.importlinter`
- Test: `tests/unit/test_agent_init.py`
- Test: `tests/unit/test_agent_host_conformance.py`
- Test: `tests/integration/test_tui_detail.py`
- Test: `tests/integration/test_tui_commands.py`
- Test: `tests/snapshots/test_tui_snapshots.py`
- Create: `e2e/test_agent_integration_explore.py`

### Protocol wording

The generated protocol inserts this phase before Assess:

```text
Understand — For a significant or unfamiliar edit, first use equivalent current repository context
already supplied by the host. If none is available, run `pebra explore` with the task, relevant symbols,
or target files before assessment. Do not repeat equivalent exploration. Treat the result as descriptive
repository context only: it does not authorize an edit and is not trusted PEBRA scoring evidence. If
exploration is unavailable, continue with the host's ordinary repository search/read tools, then assess
the exact candidate.
```

`PROTOCOL_VERSION` increments by one. Claude and Codex full skill bodies remain byte-identical.

### TDD steps

- [x] **Step 1: Write protocol regression tests**

Assert the exact Understand text, fallback instruction, advisory-only boundary, no-repeat rule, new version, byte-identical full skills, and presence in every supported host projection. Also assert the generated protocol never names `codegraph`, MCP, a prompt hook, or a provider selector. This host-neutral wording prevents Claude's optional pre-supplied graph context from causing a redundant PEBRA exploration while preserving identical Claude/Codex skill bodies.

- [x] **Step 2: Run protocol tests and confirm red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_init.py tests\unit\test_agent_host_conformance.py -q
```

Expected: old protocol version/text fails the new assertions.

- [x] **Step 3: Update the single protocol body**

Change only `_PROTOCOL_BODY` and the version constant. Preserve the existing validation-first installation, managed markers, hook ownership, and non-negotiables.

- [x] **Step 4: Add explicit TUI exploration injection**

Only if M5 proved preparation freshness, bounded cache-only mutation, and byte-stable query calls, extend the launch boundary as:

```python
def run_observatory(
    context: ObservatoryContext,
    *,
    explorer: RepositoryExplorer | None = None,
) -> None:
    """Run the Observatory with an optional injected descriptive explorer."""
```

The CLI/composition boundary constructs the explorer. `pebra.tui` imports only the port/type surface—not the concrete adapter or `pebra.composition`.

If M5 found that query calls write, or preparation can alter anything outside the resolved `.codegraph/` cache, keep `explorer=None`; the TUI shows `Repository exploration unavailable in read-only Observatory` and no provider subprocess is reachable.

- [x] **Step 5: Load detail impact asynchronously**

When allowed, detail shows an explicit `x Explore impact` action. It does not run on mount, automatic refresh, or row selection. Pressing `x` visibly prepares/reconciles the existing derived graph cache, then runs the byte-stable query against that snapshot in one single-flight worker; repeated presses while busy are ignored. Leaving detail prevents late results from touching an unmounted screen.

Render bounded context, dependent files, affected tests, snapshot HEAD/scope freshness, provider version, warnings, and truncation. Preserve the last good result on a later failure. Nothing is fed back into assessment history or scores.

- [x] **Step 6: Test trust and lifecycle boundaries**

Prove these exact behaviors:

- `test_detail_never_explores_on_mount`
- `test_five_second_refresh_never_calls_explorer`
- `test_explicit_explore_is_single_flight`
- `test_explicit_explore_prepares_once_then_queries_snapshot`
- `test_late_explore_result_cannot_touch_popped_screen`
- `test_explore_failure_preserves_assessment_detail`
- `test_exploration_result_never_enters_store_or_scores`

- [x] **Step 7: Document the explicit TUI action when it is enabled**

If M5 permits TUI exploration, add `x` to `docs/PEBRA_COMMAND_REFERENCE.md` as an assessment-detail-only key and describe its visible prepare-then-query behavior, single-flight rule, and absence from automatic refresh. If M5 vetoes the integration, document the unavailable state instead and do not advertise the key. Pin the product-defined TUI key table against the live PEBRA bindings without treating Textual's inherited bindings as PEBRA API.

- [x] **Step 8: Run protocol/TUI E2E and full gates**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_agent_init.py tests\unit\test_agent_host_conformance.py tests\integration\test_tui_detail.py tests\integration\test_tui_commands.py e2e\test_agent_integration_explore.py -q
.\.venv\Scripts\python.exe -m pytest tests\snapshots --snapshot-update
.\.venv\Scripts\python.exe -m pytest tests\snapshots e2e\test_boundary_discipline.py -q
.\.venv\Scripts\nox.exe -s tests lint e2e-fast
```

Expected: installed host artifacts contain the same no-repeat Understand contract; TUI exploration remains explicit, never runs from refresh/mount, and confines any preparation write to the derived graph cache.

- [x] **Step 9: Commit**

```powershell
git add pebra/cli/agent_init.py pebra/core/agent_hosts.py docs/GATE_CONTRACT.md pebra/cli/tui.py pebra/tui .importlinter tests/unit/test_agent_init.py tests/unit/test_agent_host_conformance.py tests/integration/test_tui_detail.py tests/integration/test_tui_commands.py tests/snapshots e2e/test_agent_integration_explore.py
git add -f docs/PEBRA_COMMAND_REFERENCE.md
git commit -m "feat: teach agents provider-neutral repository exploration"
```

### STOP FOR REVIEW 6

Report generated Claude/Codex artifact parity, protocol version, no-repeat/fallback wording, TUI preparation/query boundary, lifecycle tests, E2E result, and snapshots. Do not start Milestone 7 without approval.

---

## Milestone 7 — Demo Isolation, Documentation, Distribution, and Experiment Alignment

### Deliverable

Demo data cannot contaminate a real repository ledger; all commands and new behavior are documented and distribution-tested; deterministic experiment infrastructure reflects production behavior. No paid run is performed.

### Files

- Create: `scripts/demo_observatory.py`
- Create: `tests/integration/test_demo_observatory.py`
- Modify: `README.md`
- Modify locally (gitignored; not committed): `DEVELOPMENT.md`
- Modify: `CONTRIBUTING.md`
- Modify: `SECURITY.md`
- Modify: `docs/PEBRA_COMMAND_REFERENCE.md`
- Modify: `scripts/verify_distribution.py`
- Modify: `tests/unit/test_distribution_verifier.py`
- Modify: `tests/unit/test_cli_help.py`
- Modify: `e2e/experiments/agent_ab/tools/advisory_check_real.py`
- Modify: `e2e/experiments/agent_ab/tools/advisory_contract.py`
- Modify: `e2e/experiments/agent_ab/runners/run_gate.py`
- Modify: `e2e/experiments/agent_ab/tests/test_advisory_shape.py`
- Modify: `e2e/experiments/agent_ab/tests/test_assay_wiring.py`
- Modify: `e2e/experiments/agent_ab/tests/test_run_gate.py`

### Demo contract

```text
python -m scripts.demo_observatory [--tui | --dashboard] [--keep]
```

The helper creates a `TemporaryDirectory`, a dedicated SQLite file, and a synthetic `repo_demo_<digest>` identity. It passes `--read-only --db <temp-db> --repo-id <demo-id>` and sets a visible `DEMO` label. It never opens or writes `<checkout>/.pebra/pebra.db`. Without `--keep`, cleanup occurs after the UI exits; with `--keep`, the helper prints the explicit retained path.

Do not add a production `pebra --demo` flag in this milestone.

### TDD steps

- [x] **Step 1: Write demo-isolation tests**

Use a temporary Git repo containing a sentinel `.pebra/pebra.db`. Prove the complete tree remains byte-identical after demo creation and launch preparation. Also prove the demo repo ID, database path, and visible label are distinct from the real checkout.

- [x] **Step 2: Implement the smallest demo helper**

Reuse public assessment/store construction helpers where possible. Do not copy the repository's existing `.pebra/pebra.db`; generate purpose-built rows with distinct tasks, files, decisions, scores, commits, timestamps, and outcomes so the dashboard does not falsely look constant.

- [x] **Step 3: Correct and consolidate documentation**

Update all existing docs to:

- link `docs/PEBRA_COMMAND_REFERENCE.md` as the exhaustive command source;
- document `pebra explore` and the Understand phase;
- document that explicit graph-backed commands may reconcile only an existing same-worktree `.codegraph/` cache, while TUI/dashboard timers never do;
- document custom `codegraph.json` exclusions as operator-owned scope controls, not freshness controls, and explain the graph-scope digest shown in provenance;
- distinguish released, editable, packaged-dev, demo, dashboard, and TUI modes;
- replace hard-coded `pebra-0.1.1` wheel syntax with a version-independent wheel lookup;
- update `SECURITY.md` from `0.1.x` to the current supported development line;
- document that `pebra_compare` exists only through MCP;
- document the hidden `gate-hook --capabilities` handshake as internal, not user workflow;
- document `python -m scripts.demo_observatory` under developer/demo utilities while explicitly stating that it is not a root `pebra` command;
- audit every `console`, `powershell`, `cmd`, and `bash` block for the shell-compatibility policy and provide equivalents where activation, environment variables, piping, globbing, or command substitution differ;
- regenerate the command/session inventory from the live parser and `nox --list`, failing tests on a missing, extra, renamed, or still-marked-unshipped command;
- preserve the maintainer authorization gate for every release mutation.

`DEVELOPMENT.md` is a local-only convenience copy. The committed `README.md` and
`docs/PEBRA_COMMAND_REFERENCE.md` carry the public graph/preparation guidance.

- [x] **Step 4: Strengthen distribution verification**

The installed-wheel verifier must assert:

```text
pebra help explore
pebra explore --help
python -m pebra help explore
```

It must also import `GraphSnapshot` plus the new exploration dataclass/port, construct the CLI parser without eagerly importing the CodeGraph adapter, and verify that all packaged docs/assets required by the TUI still ship.

- [x] **Step 5: Align the deterministic experiment last**

The experiment remains a subprocess consumer of public PEBRA surfaces. Update its supported protocol/design hash and treatment instructions to include the production no-repeat Understand phase. Keep the model-facing write result exactly `{ok, blocked, reason}`. Exploration output must not identify the treatment arm, product, provider, oracle, or experiment. Record graph-scope digest only as harness-side cohort metadata; never expose it or provider identity to the model.

Do not pool pre-change/post-change runs or runs from different graph-scope digests. Do not alter the positive-control tier. Do not run a paid/provider-backed trial.

- [x] **Step 6: Run deterministic experiment tests**

```powershell
.\.venv\Scripts\python.exe -m pytest e2e\experiments\agent_ab\tests -q
.\.venv\Scripts\python.exe -m pytest e2e\test_boundary_discipline.py -q
```

Expected: the public subprocess boundary, blinding vocabulary, treatment shape, and production gate schema remain locked.

- [x] **Step 7: Run final local release-quality gates**

```powershell
.\.venv\Scripts\nox.exe -s tests lint e2e-fast
.\.venv\Scripts\nox.exe -s dev-package
.\.venv\Scripts\nox.exe -s mcp-smoke core-only
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\twine.exe check dist\*
.\.venv\Scripts\python.exe scripts\verify_distribution.py archives dist
```

Expected: all deterministic local lanes pass. `nox -s e2e-ab` is not run.

- [ ] **Step 8: Obtain remote cross-platform evidence only after push authorization**

Required GitHub jobs:

```text
Tests (ubuntu-latest)
Tests (windows-latest)
Tests (macos-latest)
Lint and architecture contracts
Build distributions
Installed wheel and CodeGraph (ubuntu-latest)
Installed wheel and CodeGraph (windows-latest)
Installed wheel and CodeGraph (macos-latest)
Playwright dashboard
Gitleaks event/full-history scan
```

Stop on any failure. Passing these jobs authorizes review, not tagging or publishing.

- [x] **Step 9: Commit the final milestone without releasing**

```powershell
git add scripts/demo_observatory.py tests/integration/test_demo_observatory.py README.md CONTRIBUTING.md SECURITY.md scripts/verify_distribution.py tests/unit/test_distribution_verifier.py tests/unit/test_cli_help.py e2e/experiments/agent_ab
git add -f docs/PEBRA_COMMAND_REFERENCE.md docs/superpowers/plans/2026-07-20-observatory-identity-and-exploration.md
git commit -m "docs: finalize observatory exploration workflow"
```

### STOP FOR REVIEW 7

Report demo tree byte-preservation, documentation parity, installed-wheel verification, deterministic experiment results, full local gates, and remote matrix evidence if authorization allowed a push. Do not tag, dispatch a release, approve PyPI, or run the paid experiment without a new explicit authorization.

---

## Final Acceptance Matrix

| Requirement | Proof |
| --- | --- |
| Users know what `asm_N` assessed | Ledger/detail show task, target, action, time, commit, and fingerprint when available. |
| Historical uncertainty is honest | Every target has provenance; unavailable data remains unavailable. |
| Audit integrity is preserved | Raw rows remain stored and addressable; new timestamp is hash-covered; legacy chains pass. |
| Repeats do not hide different edits | Grouping requires exact fingerprint and identical rendered semantics; raw is default. |
| `HEAD` is not misrepresented | Stored history is labelled `latest assessed`; gate continues fresh independent Git checks. |
| Graph evidence matches repository state | Clean checkout/pull and config-only transitions force one same-worktree reconcile; stable HEAD/config fences bind the accepted snapshot. |
| Graph scope is auditable | Assessment provenance records config and graph-scope digests; graph-derived learning never pools mixed scopes. |
| Exploration is provider-neutral | Public port/result and `pebra explore` contain no CodeGraph-only schema. |
| Exploration cannot authorize edits | No imports/data flow into scoring, gate, sanction, verification, learning, or promotion. |
| TUI read-only promise holds | No graph call on refresh/mount; explicit impact is enabled only after preparation confinement and query byte-stability proofs. |
| Agents learn the workflow without duplicate work | Generated Claude/Codex protocols share the versioned no-repeat Understand phase and fallback. |
| Demo data cannot contaminate real state | Temporary explicit DB and byte-identical checkout-tree test. |
| Experiments reflect production | Deterministic assay tests run last with a new design hash; no paid run is implied. |
| Cross-platform artifact is proven | Three-OS source and installed-wheel jobs plus Playwright and Gitleaks succeed. |

## Deliberate Non-Goals

- No database-row deduplication.
- No reconstruction of missing historical time.
- No inferred fingerprint for an unbound legacy candidate.
- No general graph-plugin framework.
- No CodeGraph MCP requirement.
- No automatic graph installation, initialization, borrowed-worktree repair, or `codegraph.json` creation. Explicit graph-backed operations may reconcile only an existing same-worktree derived index; TUI/dashboard timers never do.
- No guessed default `codegraph.json` exclusions; CodeGraph's built-ins and operator-owned project configuration remain authoritative.
- No exploration-derived risk score.
- No TUI approval or mutation workflow.
- No efficacy claim from deterministic tests or a small number of seeds.
- No release or paid experiment as an implicit consequence of completing this plan.
