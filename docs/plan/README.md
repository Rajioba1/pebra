# PEBRA Implementation Plan

This folder is the **build plan** for PEBRA. It mirrors the architecture and spec and turns
them into an executable, phase-by-phase implementation sequence.

- **`IMPLEMENTATION_PLAN.md`** — the authoritative plan: package layout, internal contracts,
  cross-cutting setup, and the Phase 0 → Phase 7 build order with per-phase modules, ADs
  realized, tests, and exit criteria.

## Source of truth

The plan is a *companion*, not a replacement. When the plan and the design docs disagree, the
design docs win and the plan must be corrected:

- `../PEBRA_Architecture.md` — **how to build** (layering, §3 module table [authoritative per
  AD-10], §5 math, §6 gates, §10 store, §12 deps, §13 ADs, §14 build sequence, §15 success criteria).
- `../PEBRA_Report_Final.md` — **what PEBRA computes** (the spec; §-numbered contracts).

## The one rule

`core/` is the pure deterministic engine: it imports only the pure standard-library subset and
other `core/` modules — never `ports/`, `adapters/`, `app/`, surfaces, pip packages, or
I/O-oriented stdlib (`sqlite3`, `subprocess`, `argparse`). This is enforced mechanically from
commit 1 by `import-linter` plus an AST-walk purity test. Every other rule in this plan is
downstream of that one.

## Status

Plan — nothing implemented yet. Build one phase at a time; do not start phase *N+1* until
phase *N*'s exit criteria and tests pass.
