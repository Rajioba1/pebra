# Changelog

## 0.4.1 — 2026-07-26

### Changed

- The browser Observatory uses accessible file/symbol colours, larger graph labels, stronger visual
  hierarchy, a 12px label floor, and WCAG-compliant muted text.
- CoSE spacing now scales with graph size, and explicit Auto actions animate once while live refreshes
  remain stable and reduced-motion preferences are respected.
- Assessment history now prioritizes columns responsively while preserving every hidden value in the
  row detail view.

### Reliability and accessibility

- Initial tab loads use reduced-motion-aware skeletons without replaying them during live refreshes.
- Distribution bars expose a text summary to assistive technology, and graph relayouts keep
  zoom-disclosed labels, viewport sizing, and scroll restoration synchronized.

## 0.4.0 — 2026-07-26

### Added

- The browser Observatory now opens on a full-size codebase graph, with deterministic Auto layout,
  explicit zoom, Fit, 100%, and fullscreen controls, plus a collapsed, lazy-loaded hotspot ranking.
- The live agent assay can reuse isolated, locked specimen slots across trials while retaining
  dependency and CodeGraph caches.

### Changed

- Browser Overview and History are combined into Activity; legacy dashboard hashes continue to open
  the merged view.
- Assay slots reset to the pinned specimen HEAD between subjects and use the production
  status/sync/status graph boundary instead of recloning and fully indexing every arm.

### Reliability and safety

- Persistent assay slots use deterministic arm-neutral assignment, exclusive leases, clean-source
  admission, generation-bound receipts, protected dependency/graph state, and rebuild-once graph
  recovery.
- Dashboard tabs now implement keyboard navigation and roving focus, hotspot actions are keyboard
  accessible, unavailable graphs show setup guidance without an empty stage, and resize/fullscreen
  transitions keep the Cytoscape viewport synchronized.

### Evidence boundary

- Persistent workspaces change assay execution cost and production realism, not the estimand or
  efficacy evidence. A paid run is still required before drawing a new treatment-effect conclusion.

## 0.3.1 — 2026-07-25

### Fixed

- The live agent assay now unwraps the public `pebra explore --json` envelope before validating and
  delivering graph-backed repository context.
- Graph-arm readiness and subject-facing context now consume the same validated nested payload, so a
  healthy index is no longer rejected as unavailable before paid graph arms run.

## 0.3.0 — 2026-07-25

### Added

- Fresh graph-backed assessments now retain deterministic, bounded impact witnesses and cite dependent
  symbols and repository locations in explanations without changing numeric risk scores or decisions.
- The agent assay projects the shipped witness evidence into blinded real-advisory arms, authenticates
  the treatment version in its design hash, and records host-only delivery receipts.

### Reliability and safety

- Witness selection preserves complete impact counts, excludes internal graph node identifiers from
  public packets, and prefers valid direct edge sites over definition-location fallbacks.
- Assay preflight and telemetry use the same bounded sentence projection, so delivery receipts count
  only evidence actually shown to the subject.

### Evidence boundary

- Impact-witness delivery is process telemetry, not an efficacy endpoint. The included unpaid Zod
  preflight validates treatment wiring but does not establish agent efficacy.

## 0.2.1 — 2026-07-25

### Added

- Human-facing CLI, dashboard, and TUI surfaces can report a newer PyPI release from a bounded local
  cache, with explicit opt-out and editable-install safeguards.
- `pebra update` and `pebra update-check` provide explicit upgrade and status workflows without
  entering machine-readable assessment, gate, or MCP streams.

## 0.2.0 — 2026-07-19

### Added

- A packaged Textual Observatory launched with `pebra tui`, including a responsive assessment ledger,
  RAU gate lanes, score trends, persisted assessment details, command palette actions, and keyboard help.
- Claude and Codex integration materialization through `pebra agent-init`, with inspection mode,
  always-loaded safety rules, managed-file preservation, and host-specific enforcement reporting.
- A versioned gate contract and exact candidate-binding protocol for pre-edit agent integrations.

### Changed

- Candidate holds now return actionable risk and benefit context while preserving the user's goal;
  installed hooks never let an agent self-answer a human-review request.
- Observatory refreshes preserve selection, scroll, focus, and open views instead of resetting user
  interaction state.
- Distribution verification now checks installed agent artifacts, TUI assets, CLI behavior, and host
  registry conformance independently from the source checkout.

### Reliability and safety

- Agent integration setup validates all managed destinations before writing and rejects malformed,
  redirected, hard-linked, or unsafe paths without partial installation.
- Experiment consumers validate the production gate schema before provider work, bind resumes to the
  complete experiment design, and attribute assessments only after successful exact-candidate writes.
- Windows experiment artifacts tolerate transient reader locks while retaining atomic replacement.

### Evidence boundary

- The included one-seed multi-arm experiment is diagnostic evidence only. It demonstrated harm
  avoidance for the tested PEBRA arms but does not authorize a general efficacy claim.
