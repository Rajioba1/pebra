# Changelog

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
